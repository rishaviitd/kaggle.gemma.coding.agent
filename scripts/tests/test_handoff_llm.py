import asyncio
import copy
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / 'runs'))
import handoff_llm as h  # noqa: E402
import replay_llm as r  # noqa: E402
from google.adk.models.base_llm import BaseLlm  # noqa: E402
from google.adk.models.llm_request import LlmRequest  # noqa: E402
from google.adk.models.llm_response import LlmResponse  # noqa: E402
from google.genai import types  # noqa: E402


class FakeLlm(BaseLlm):
    reply: str
    seen_models: list = []

    async def generate_content_async(self, llm_request, stream=False):
        self.seen_models.append(llm_request.model)
        yield LlmResponse(content=types.Content(role='model', parts=[types.Part(text=self.reply)]))


def agent_request(model='alias'):
    tool = types.Tool(function_declarations=[types.FunctionDeclaration(name='get_status', description='x')])
    return LlmRequest(model=model, contents=[types.Content(role='user', parts=[types.Part(text='task')])],
                      config=types.GenerateContentConfig(tools=[tool]))


def summary_request(model='alias'):
    prompt = h.SUMMARY_PROMPT + ' Summarize it.'
    return LlmRequest(model=model, contents=[types.Content(role='user', parts=[types.Part(text=prompt)])])


def call(llm, request):
    async def go():
        return [r async for r in llm.generate_content_async(request)]
    return asyncio.run(go())[0].content.parts[0].text


def make(switch_after):
    student = FakeLlm(model='student-model', reply='s')
    teacher = FakeLlm(model='teacher-model', reply='t')
    return HandoffFor(student, teacher, switch_after), student, teacher


def HandoffFor(student, teacher, switch_after):
    return h.HandoffLlm(model='student-model', student=student, teacher=teacher, switch_after=switch_after)


class HandoffLlmTests(unittest.TestCase):
    def test_first_calls_go_to_the_student_then_the_teacher(self):
        llm, student, teacher = make(2)
        texts = [call(llm, agent_request()) for _ in range(4)]
        self.assertEqual(texts, ['s', 's', 't', 't'])
        self.assertEqual(student.seen_models, ['student-model', 'student-model'])
        self.assertEqual(teacher.seen_models, ['teacher-model', 'teacher-model'])

    def test_summary_before_the_first_teacher_call_raises(self):
        llm, student, teacher = make(1)
        self.assertEqual(call(llm, agent_request()), 's')
        with self.assertRaisesRegex(RuntimeError, 'compaction before handoff'):
            call(llm, summary_request())
        self.assertEqual(teacher.seen_models, [])  # no teacher call was made
        self.assertEqual(llm.record()['summary_calls_before_handoff'], 1)
        self.assertEqual(llm.record()['agent_calls'], 1)

    def test_summary_after_the_teacher_started_goes_to_the_teacher_uncounted(self):
        llm, _, _ = make(1)
        self.assertEqual(call(llm, agent_request()), 's')
        self.assertEqual(call(llm, agent_request()), 't')
        self.assertEqual(call(llm, summary_request()), 't')
        self.assertEqual(llm.record(), {
            'switch_after': 1, 'agent_calls': 2, 'student_calls': 1, 'teacher_calls': 1,
            'summary_calls_before_handoff': 0,
            'student_model': 'student-model', 'teacher_model': 'teacher-model'})

    def test_run_that_ends_before_the_handoff_has_no_teacher_calls(self):
        llm, _, _ = make(5)
        call(llm, agent_request())
        record = llm.record()
        self.assertEqual((record['student_calls'], record['teacher_calls']), (1, 0))

    def test_is_summary_request_requires_no_tools_and_one_prompt(self):
        self.assertTrue(h.is_summary_request(summary_request()))
        self.assertFalse(h.is_summary_request(agent_request()))


class StopAtSwitchTests(unittest.TestCase):
    def test_teacher_is_never_called_and_hook_fires_once(self):
        calls = []
        student, teacher = FakeLlm(model='student-model', reply='s'), FakeLlm(model='teacher-model', reply='t')
        llm = h.HandoffLlm(model='student-model', student=student, teacher=teacher, switch_after=2,
                           stop_at_switch=True, on_switch=lambda: calls.append('switch'))
        self.assertEqual([call(llm, agent_request()) for _ in range(2)], ['s', 's'])
        with self.assertRaises(h.HandoffStop):
            call(llm, agent_request())
        self.assertEqual(calls, ['switch'])
        self.assertTrue(llm.stopped)
        self.assertEqual(teacher.seen_models, [])

    def test_stop_is_an_llm_calls_limit_error_with_a_safe_message(self):
        from google.adk.agents.invocation_context import LlmCallsLimitExceededError
        self.assertTrue(issubclass(h.HandoffStop, LlmCallsLimitExceededError))
        message = str(h.HandoffStop(3)).lower()
        for phrase in ('rate limit', 'overloaded', 'quota exceeded', '429', '500', '502', '503', '504'):
            self.assertNotIn(phrase, message)
        self.assertEqual(h.HandoffStop(3).switch_after, 3)

    def test_stopped_is_false_until_the_stop(self):
        student, teacher = FakeLlm(model='student-model', reply='s'), FakeLlm(model='teacher-model', reply='t')
        llm = h.HandoffLlm(model='student-model', student=student, teacher=teacher, switch_after=1,
                           stop_at_switch=True)
        self.assertFalse(llm.stopped)
        call(llm, agent_request())
        self.assertFalse(llm.stopped)

    def test_without_stop_the_teacher_still_takes_over(self):
        llm, _, teacher = make(1)
        self.assertEqual([call(llm, agent_request()) for _ in range(2)], ['s', 't'])
        self.assertFalse(llm.stopped)
        self.assertEqual(teacher.seen_models, ['teacher-model'])


class StudentResponseTests(unittest.TestCase):
    def test_student_responses_are_recorded_in_order_and_teacher_ones_are_not(self):
        llm, _, _ = make(2)
        for _ in range(3):
            call(llm, agent_request())
        responses = llm.student_responses()
        self.assertEqual(len(responses), 2)
        self.assertEqual([r['content']['parts'][0]['text'] for r in responses], ['s', 's'])

    def test_returned_list_is_a_copy(self):
        llm, _, _ = make(2)
        call(llm, agent_request())
        llm.student_responses().clear()
        self.assertEqual(len(llm.student_responses()), 1)


class SharedStateAcrossCopiesTests(unittest.TestCase):
    """ADK/pydantic copies the registered model; the running copy must report to the original."""

    def check_copy_shares_state(self, copier):
        llm, _, _ = make(3)
        running = copier(llm)
        call(running, agent_request())
        call(running, agent_request())
        self.assertEqual(llm.record()['agent_calls'], 2)
        self.assertEqual(len(llm.student_responses()), 2)

    def test_deepcopy_shares_state_with_the_original(self):
        self.check_copy_shares_state(copy.deepcopy)

    def test_model_copy_deep_shares_state_with_the_original(self):
        self.check_copy_shares_state(lambda m: m.model_copy(deep=True))

    def test_model_copy_shallow_shares_state_with_the_original(self):
        self.check_copy_shares_state(lambda m: m.model_copy())

    def test_stop_through_a_copy_sets_the_original_stopped(self):
        student, teacher = FakeLlm(model='student-model', reply='s'), FakeLlm(model='teacher-model', reply='t')
        llm = h.HandoffLlm(model='student-model', student=student, teacher=teacher, switch_after=1,
                           stop_at_switch=True)
        running = copy.deepcopy(llm)
        call(running, agent_request())
        with self.assertRaises(h.HandoffStop):
            call(running, agent_request())
        self.assertTrue(llm.stopped)
        self.assertTrue(running.stopped)


class SwitchPointTests(unittest.TestCase):
    def test_one_point_per_bin_and_deterministic(self):
        points = h.draw_switch_points(3, 1, 12, 'seed:task')
        self.assertEqual(points, h.draw_switch_points(3, 1, 12, 'seed:task'))
        for point, (low, high) in zip(points, [(1, 4), (5, 8), (9, 12)]):
            self.assertTrue(low <= point <= high, (point, low, high))

    def test_more_attempts_than_values_still_valid(self):
        points = h.draw_switch_points(5, 2, 3, 1)
        self.assertEqual(len(points), 5)
        self.assertTrue(all(2 <= p <= 3 for p in points))

    def test_rejects_bad_ranges(self):
        for args in [(0, 1, 5, 1), (2, 0, 5, 1), (2, 6, 5, 1)]:
            with self.assertRaises(ValueError):
                h.draw_switch_points(*args)


class ArgumentTests(unittest.TestCase):
    def test_both_flags_or_neither(self):
        h.validate_handoff_args('vllm', None, None)
        h.validate_handoff_args('vllm', 'claude-haiku-5-5', 4)
        for args in [('vllm', 'claude-haiku-5-5', None), ('vllm', None, 4),
                     ('anthropic', 'claude-haiku-5-5', 4), ('vllm', 'claude-haiku-5-5', 0)]:
            with self.assertRaises(ValueError):
                h.validate_handoff_args(*args)


class FingerprintAndRegistryTests(unittest.TestCase):
    def test_fingerprint_changes_with_content_and_ignores_caches(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = Path(tmp)
            (agent / 'agent.yaml').write_text('model: m\n')
            first = h.config_fingerprint(agent, 'docker')['agent_config_sha256']
            (agent / '__pycache__').mkdir()
            (agent / '__pycache__' / 'x.pyc').write_bytes(b'1')
            self.assertEqual(first, h.config_fingerprint(agent, 'docker')['agent_config_sha256'])
            (agent / 'agent.yaml').write_text('model: other\n')
            self.assertNotEqual(first, h.config_fingerprint(agent, 'docker')['agent_config_sha256'])
            self.assertEqual(h.config_fingerprint(agent, 'subprocess')['sandbox'], 'subprocess')

    def test_registry_shares_one_handoff_instance_across_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = Path(tmp)
            (agent / 'agent.yaml').write_text('model: gemma-4-e4b-it-qat-w4a16-ct\n')
            (agent / 'sub.yaml').write_text('model: "other-alias"\n')
            models, handoff = h.build_handoff_registry(
                student_base='http://127.0.0.1:1', student_key='k', student_model='openai/gemma4',
                teacher_base='http://127.0.0.1:2', teacher_key='k', teacher_model='anthropic/claude-haiku-5-5',
                agent_dir=agent, switch_after=3)
            self.assertIs(models.get('gemma-4-e4b-it-qat-w4a16-ct'), handoff)
            self.assertIs(models.get('other-alias'), handoff)
            self.assertIs(models.get('openai/gemma4'), handoff)
            self.assertEqual(h.submission_aliases('x', agent),
                             {'x', 'gemma-4-e4b-it-qat-w4a16-ct', 'other-alias'})


class RewriterTests(unittest.TestCase):
    BODY = {'temperature': 0.2, 'tools': [{'name': 'read_file'}], 'max_tokens': 8192,
            'chat_template_kwargs': {}, 'thinking': {'type': 'enabled', 'budget_tokens': 10}}

    def test_adaptive_thinking(self):
        out = h.teacher_rewriter('adaptive')(dict(self.BODY))
        self.assertEqual(out, {'thinking': {'type': 'adaptive', 'display': 'summarized'},
                               'tools': self.BODY['tools'], 'max_tokens': 8192})

    def test_thinking_off_removes_the_key(self):
        out = h.teacher_rewriter('off')(dict(self.BODY))
        self.assertEqual(out, {'tools': self.BODY['tools'], 'max_tokens': 8192})

    def test_unknown_mode_rejected(self):
        with self.assertRaises(ValueError):
            h.teacher_rewriter('sometimes')


class EventRecorderTests(unittest.TestCase):
    class Event:
        def __init__(self, event_id, partial=None):
            self.id = event_id
            self.partial = partial

        def model_dump(self, mode='json'):
            return {'id': self.id, 'partial': self.partial}

    def test_skips_partials_and_duplicate_ids_and_keeps_order(self):
        recorder = h.EventRecorder()
        for event in [self.Event('a', partial=True), self.Event('a'), self.Event('a'),
                      self.Event('b', partial=False), None, self.Event('c')]:
            recorder.record(event)
        self.assertEqual([e['id'] for e in recorder.events], ['a', 'b', 'c'])

    def test_records_plain_dicts_from_model_dump(self):
        recorder = h.EventRecorder()
        recorder.record(self.Event('x'))
        self.assertEqual(recorder.events, [{'id': 'x', 'partial': None}])


class StopRegistryTests(unittest.TestCase):
    def agent_dir(self, tmp):
        agent = Path(tmp)
        (agent / 'agent.yaml').write_text('model: alias\n')
        return agent

    def test_stop_mode_teacher_is_a_stub_that_refuses_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, handoff = h.build_handoff_registry(
                student=FakeLlm(model='student-model', reply='s'), student_model='openai/gemma4',
                teacher_model='anthropic/claude-haiku-5-5', agent_dir=self.agent_dir(tmp),
                switch_after=2, stop_at_switch=True, on_switch=lambda: None)
        self.assertIsInstance(handoff.teacher, h.StoppedTeacher)
        self.assertTrue(handoff.stop_at_switch)
        with self.assertRaisesRegex(RuntimeError, 'stop mode'):
            call(handoff.teacher, agent_request())

    def test_resume_student_override_is_used_as_given(self):
        replay = r.ReplayLlm(model='openai/gemma4', responses=[])
        with tempfile.TemporaryDirectory() as tmp:
            _, handoff = h.build_handoff_registry(
                student=replay, teacher_base='http://teacher', teacher_key='k',
                teacher_model='anthropic/claude-haiku-5-5', student_model='openai/gemma4',
                agent_dir=self.agent_dir(tmp), switch_after=1)
        self.assertIs(handoff.student, replay)
        self.assertNotIsInstance(handoff.teacher, h.StoppedTeacher)


if __name__ == '__main__':
    unittest.main()
