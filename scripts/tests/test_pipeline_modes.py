import asyncio
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runs'))
import handoff_llm  # noqa: E402
import handoff_snapshot  # noqa: E402
import task_pipeline  # noqa: E402
from google.adk.events import Event  # noqa: E402
from google.adk.models.base_llm import BaseLlm  # noqa: E402
from google.adk.models.llm_request import LlmRequest  # noqa: E402
from google.adk.models.llm_response import LlmResponse  # noqa: E402
from google.genai import types  # noqa: E402

TASK = 't1'


class PipelineModeArgs(unittest.TestCase):
    """arguments() reads ROOT/data/<split>/manifest.csv and ROOT/src/<submission>/agent.yaml."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / 'src' / 'agent').mkdir(parents=True)
        (root / 'src' / 'agent' / 'agent.yaml').write_text('model: alias\n')
        (root / 'data' / 'val').mkdir(parents=True)
        (root / 'data' / 'val' / 'manifest.csv').write_text(
            'task_id,snapshot_file,graph_file,embedding_file,wheels_dir\n'
            f'{TASK},a,b,c,d\n' 't2,a,b,c,d\n')
        self.patch_root = mock.patch.object(task_pipeline, 'ROOT', root)
        self.patch_root.start()
        self.submission = root / 'src' / 'agent'

    def tearDown(self):
        self.patch_root.stop()
        self.tmp.cleanup()

    def parse(self, *extra, tasks=('--task-id', TASK)):
        # Mirrors arguments()'s real requirements: --submission and a task selection.
        argv = ['task_pipeline.py', '--split', 'val', *tasks, '--provider', 'vllm',
                '--sandbox', 'subprocess', '--submission', str(self.submission), *extra]
        err = io.StringIO()
        with mock.patch.object(sys, 'argv', argv), contextlib.redirect_stderr(err):
            try:
                return task_pipeline.arguments(), ''
            except SystemExit:
                return None, err.getvalue()

    def assertRejected(self, message, *extra):
        args, err = self.parse(*extra)
        self.assertIsNone(args, 'expected argument error')
        self.assertIn(message, err)

    def test_stop_mode_needs_teacher_model_and_switch_point(self):
        self.assertRejected('--handoff-teacher-model', '--handoff-stop-after-switch', '--handoff-after', '3')
        self.assertRejected('--handoff-after', '--handoff-stop-after-switch',
                            '--handoff-teacher-model', 'claude-haiku-5-5')

    def test_stop_mode_accepted_with_teacher_and_switch_point(self):
        args, err = self.parse('--handoff-stop-after-switch', '--handoff-teacher-model',
                               'claude-haiku-5-5', '--handoff-after', '3')
        self.assertIsNotNone(args, err)
        self.assertTrue(args.handoff_stop_after_switch)
        self.assertEqual(args.handoff_after, 3)

    def test_resume_and_stop_are_exclusive(self):
        self.assertRejected('mutually exclusive', '--handoff-stop-after-switch', '--handoff-resume',
                            'snap', '--handoff-teacher-model', 'claude-haiku-5-5',
                            '--handoff-after', '3')

    def test_resume_needs_teacher_model(self):
        self.assertRejected('--handoff-teacher-model', '--handoff-resume', 'snap')

    def test_resume_takes_switch_point_from_snapshot(self):
        args, err = self.parse('--handoff-resume', 'snap', '--handoff-teacher-model', 'claude-haiku-5-5')
        self.assertIsNotNone(args, err)
        self.assertEqual(args.handoff_resume, Path('snap'))
        self.assertIsNone(args.handoff_after)

    def test_resume_requires_the_vllm_provider(self):
        self.assertRejected('--provider vllm', '--handoff-resume', 'snap',
                            '--handoff-teacher-model', 'claude-haiku-5-5', '--provider', 'openai')

    def test_resume_requires_the_subprocess_sandbox(self):
        self.assertRejected('--sandbox subprocess', '--handoff-resume', 'snap',
                            '--handoff-teacher-model', 'claude-haiku-5-5', '--sandbox', 'docker')

    def test_resume_is_single_task_only(self):
        args, err = self.parse('--handoff-resume', 'snap', '--handoff-teacher-model',
                               'claude-haiku-5-5', tasks=('--task-ids', TASK, 't2'))
        self.assertIsNone(args)
        self.assertIn('exactly one task', err)



class RecordingContextTests(unittest.TestCase):
    class Event:
        def __init__(self, event_id, partial=None):
            self.id = event_id
            self.partial = partial
            self.content = None

        def model_dump(self, mode='json'):
            return {'id': self.id, 'partial': self.partial}

    def test_records_non_partial_events_once_before_the_base_class_runs(self):
        seen_by_base = []
        original = task_pipeline.SwegemmaContext.handle_adk_event

        def spy(self, event, agent_name=None):
            seen_by_base.append(list(self.recorded_events))
            return original(self, event, agent_name=agent_name)

        with mock.patch.object(task_pipeline.SwegemmaContext, 'handle_adk_event', spy):
            context = task_pipeline.RecordingContext(docker_manager=None)
            context.handle_adk_event(self.Event('p', partial=True))
            context.handle_adk_event(self.Event('a'))
            context.handle_adk_event(self.Event('a'))  # the second dispatch of the same event
        self.assertEqual([e['id'] for e in context.recorded_events], ['a'])
        self.assertEqual([len(x) for x in seen_by_base], [0, 1, 1])



class SwitchPointCheck(unittest.TestCase):
    GOOD = {'switch_after': 2, 'student_calls': 2}

    def test_matching_counts_pass(self):
        task_pipeline.check_switch_point(self.GOOD, ['r1', 'r2'], [{'id': 'e'}])

    def test_response_count_mismatch_is_refused_with_the_numbers(self):
        with self.assertRaisesRegex(RuntimeError, 'switch_after=2.*student_responses=0'):
            task_pipeline.check_switch_point(self.GOOD, [], [{'id': 'e'}])

    def test_student_call_count_mismatch_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, 'student_calls=1'):
            task_pipeline.check_switch_point({'switch_after': 2, 'student_calls': 1},
                                             ['r1', 'r2'], [{'id': 'e'}])

    def test_empty_session_events_are_refused(self):
        with self.assertRaisesRegex(RuntimeError, 'session_events=0'):
            task_pipeline.check_switch_point(self.GOOD, ['r1', 'r2'], [])


class FakeLiteLlm(BaseLlm):
    """Stands in for the vLLM student and the Haiku teacher; records which models were called."""

    calls: list = []

    async def generate_content_async(self, llm_request, stream=False):
        FakeLiteLlm.calls.append(self.model)
        yield LlmResponse(content=types.Content(role='model', parts=[types.Part(text='ok')]))


class ScriptedEvaluator:
    """Drives run_one's HandoffLlm the way the agent loop would, then returns a fake TaskResult."""

    turns = 0
    tool_output = 'out'
    emit_events = True

    def __init__(self, config):
        self.config = config
        self.docker = None

    async def evaluate_task(self, task, task_index=1, total_tasks=1, context=None):
        handoff = self.config.models.get('openai/gemma4')
        for turn in range(ScriptedEvaluator.turns):
            request = LlmRequest(model='openai/gemma4', contents=[types.Content(
                role='user', parts=[types.Part(text=f'step {turn}')])])
            try:
                async for _ in handoff.generate_content_async(request):
                    pass
            except handoff_llm.HandoffStop:
                break  # swegemma ends the episode here and returns a normal result
            event = Event(author='agent', invocation_id='inv', content=types.Content(
                role='user', parts=[types.Part(function_response=types.FunctionResponse(
                    name='run_command',
                    response={'stdout': f'{ScriptedEvaluator.tool_output}-{turn}', 'elapsed': turn}))]))
            if ScriptedEvaluator.emit_events:
                context.handle_adk_event(event)
                context.handle_adk_event(event)  # swegemma dispatches each event twice
        return SimpleNamespace(tool_calls=ScriptedEvaluator.turns, agent_patch='', resolved=False,
                               test_exit_code=None, error_message=None,
                               model_dump_json=lambda indent=2, exclude=None: '{}',
                               model_dump=lambda mode='json', exclude=None: {})


class RunOneHandoffModes(unittest.TestCase):
    """Drives run_one end to end for run A (stop) and run B (resume), with a scripted evaluator
    and a fake LLM in place of swegemma's sandbox and the real models."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.root = root
        agent = root / 'agent'
        agent.mkdir()
        (agent / 'agent.yaml').write_text('model: gemma-alias\n')
        self.agent = agent
        wheels = root / 'wheels'
        wheels.mkdir()
        (wheels / 'pkg-1.0-py3-none-any.whl').write_bytes(b'')
        graph = root / 'graph'
        graph.mkdir()
        (graph / 'repo_abc.json').write_text('{}')
        emb = root / 'emb'
        emb.mkdir()
        (emb / 'repo_abc.npz').write_bytes(b'')
        snap = root / 'snap.tgz'
        snap.write_bytes(b'')
        tasks = root / 'tasks.jsonl'
        tasks.write_text('')
        self.task = SimpleNamespace(instance_id=TASK, repo='org/repo', base_commit='abc',
                                    problem_statement='fix it', hints_text='')
        self.logs = root / 'logs'
        self.switch_after = 2
        self.constructed = []  # every LiteLlm the pipeline builds, in order
        self.args = SimpleNamespace(
            asset_rows={TASK: {'snapshot': snap, 'graph': graph / 'repo_abc.json',
                               'embeddings': emb / 'repo_abc.npz', 'wheels': wheels}},
            tasks=tasks, logs=self.logs, provider='vllm', litellm_model='openai/gemma4',
            api_base='http://127.0.0.1:9/v1', sandbox='subprocess', reasoning_effort=None,
            max_tool_calls=25, max_minutes=5, timeout_seconds=300, max_turns=None,
            max_output_tokens=None, handoff=True, handoff_teacher_model='claude-haiku-5-5',
            handoff_litellm_model='anthropic/claude-haiku-5-5', teacher_api_base='http://127.0.0.1:9',
            handoff_teacher_thinking='adaptive', handoff_after=self.switch_after,
            handoff_stop_after_switch=True, handoff_resume=None)
        self.patches = [
            mock.patch.object(task_pipeline, 'Evaluator', ScriptedEvaluator),
            mock.patch('google.adk.models.lite_llm.LiteLlm', side_effect=self.make_litellm),
        ]
        for patch in self.patches:
            patch.start()
        FakeLiteLlm.calls = []
        ScriptedEvaluator.emit_events = True

    def make_litellm(self, model, **kwargs):
        self.constructed.append(model)
        return FakeLiteLlm(model=model)

    def tearDown(self):
        for patch in self.patches:
            patch.stop()
        self.tmp.cleanup()

    def run_mode(self, handoff_snapshot=None):
        working = Path(tempfile.mkdtemp(dir=self.root))  # run_one needs a fresh working dir
        return asyncio.run(task_pipeline.run_one(
            self.args, working, self.agent, 'key', self.task, 1, 1,
            teacher_key=None, handoff_snapshot=handoff_snapshot))

    def test_run_a_stops_at_the_switch_and_writes_a_snapshot(self):
        ScriptedEvaluator.turns = self.switch_after + 1
        summary = self.run_mode()
        self.assertTrue(summary['stopped_at_switch'])
        self.assertEqual(summary['switch_after'], self.switch_after)
        snap = self.logs / TASK / 'snapshot'
        for name in handoff_snapshot.REQUIRED_FILES:
            self.assertTrue((snap / name).is_file(), name)
        events = json.loads((snap / 'session_events.json').read_text())
        self.assertEqual(len(events), self.switch_after)  # duplicate dispatch recorded once
        self.assertEqual(len(json.loads((snap / 'student_responses.json').read_text())), self.switch_after)
        self.assertEqual(FakeLiteLlm.calls, ['openai/gemma4'] * self.switch_after)
        # Run A builds only the vLLM student: no Anthropic client exists.
        self.assertEqual(self.constructed, ['openai/gemma4'])
        self.assertFalse((self.logs / TASK / 'results' / f'{TASK}.json').exists())

    def test_run_b_replays_the_snapshot_and_verifies_tool_results(self):
        ScriptedEvaluator.turns = self.switch_after + 1
        self.run_mode()
        snapshot = handoff_snapshot.read_snapshot(self.logs / TASK / 'snapshot',
                                                  agent_dir=self.agent, sandbox='subprocess')
        self.args.handoff_stop_after_switch = False
        self.args.handoff_resume = self.logs / TASK / 'snapshot'
        FakeLiteLlm.calls = []
        self.constructed = []
        ScriptedEvaluator.turns = self.switch_after
        summary = self.run_mode(handoff_snapshot=snapshot)
        self.assertTrue(summary['replay_verified'])
        self.assertEqual(FakeLiteLlm.calls, [])  # student replayed, teacher never reached here
        self.assertEqual(self.constructed, ['anthropic/claude-haiku-5-5'])  # teacher only
        self.assertTrue((self.logs / TASK / 'results' / f'{TASK}.json').is_file())

    def test_a_failed_snapshot_write_fails_the_run_loudly(self):
        ScriptedEvaluator.turns = self.switch_after + 1
        with mock.patch.object(task_pipeline, 'write_snapshot',
                               side_effect=OSError('disk full')):
            with self.assertRaisesRegex(RuntimeError, 'snapshot write failed.*disk full'):
                self.run_mode()
        self.assertFalse((self.logs / TASK / 'snapshot').exists())

    def test_a_snapshot_with_mismatched_responses_is_refused_and_leaves_no_directory(self):
        ScriptedEvaluator.turns = self.switch_after + 1
        with mock.patch.object(handoff_llm.HandoffLlm, 'student_responses', return_value=[]):
            with self.assertRaisesRegex(RuntimeError, 'snapshot write failed.*student_responses=0'):
                self.run_mode()
        self.assertFalse((self.logs / TASK / 'snapshot').exists())

    def test_a_snapshot_without_session_events_is_refused(self):
        ScriptedEvaluator.turns = self.switch_after + 1
        ScriptedEvaluator.emit_events = False
        with self.assertRaisesRegex(RuntimeError, 'snapshot write failed.*session_events=0'):
            self.run_mode()
        self.assertFalse((self.logs / TASK / 'snapshot').exists())

    def test_run_b_rejects_a_changed_tool_result(self):
        ScriptedEvaluator.turns = self.switch_after + 1
        self.run_mode()
        snapshot = handoff_snapshot.read_snapshot(self.logs / TASK / 'snapshot',
                                                  agent_dir=self.agent, sandbox='subprocess')
        ScriptedEvaluator.turns = self.switch_after
        ScriptedEvaluator.tool_output = 'different'
        try:
            with self.assertRaisesRegex(ValueError, 'differs from the snapshot'):
                self.run_mode(handoff_snapshot=snapshot)
        finally:
            ScriptedEvaluator.tool_output = 'out'


if __name__ == '__main__':
    unittest.main()
