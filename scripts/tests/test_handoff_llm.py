import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / 'runs'))
import handoff_llm as h  # noqa: E402
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

    def test_summary_calls_are_not_counted_and_follow_the_current_model(self):
        llm, _, _ = make(1)
        self.assertEqual(call(llm, agent_request()), 's')
        self.assertEqual(call(llm, summary_request()), 's')  # still before the first teacher call
        self.assertEqual(call(llm, agent_request()), 't')
        self.assertEqual(call(llm, summary_request()), 't')
        self.assertEqual(llm.record(), {
            'switch_after': 1, 'agent_calls': 2, 'student_calls': 1, 'teacher_calls': 1,
            'summary_calls_before_handoff': 1,
            'student_model': 'student-model', 'teacher_model': 'teacher-model'})

    def test_run_that_ends_before_the_handoff_has_no_teacher_calls(self):
        llm, _, _ = make(5)
        call(llm, agent_request())
        record = llm.record()
        self.assertEqual((record['student_calls'], record['teacher_calls']), (1, 0))

    def test_is_summary_request_requires_no_tools_and_one_prompt(self):
        self.assertTrue(h.is_summary_request(summary_request()))
        self.assertFalse(h.is_summary_request(agent_request()))


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


if __name__ == '__main__':
    unittest.main()
