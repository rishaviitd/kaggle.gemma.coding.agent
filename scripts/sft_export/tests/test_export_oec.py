import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import export_oec as oec  # noqa: E402
import export_sft  # noqa: E402

TOOL = {'name': 'run_command', 'description': 'Run', 'input_schema': {
    'type': 'object', 'properties': {'command': {'type': 'string'}}, 'required': ['command']}}
BASE = {'system': [{'type': 'text', 'text': 'SYSTEM'}], 'tools': [TOOL]}
USER = {'role': 'user', 'content': [{'type': 'text', 'text': 'Fix it'}]}


def use(call_id, command, text=None):
    blocks = [{'type': 'text', 'text': text}] if text else []
    return {'role': 'assistant', 'content': blocks + [
        {'type': 'tool_use', 'id': call_id, 'name': 'run_command', 'input': {'command': command}}]}


def result(call_id, body='{"status":"ok"}'):
    return {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': call_id, 'content': body}]}


def teacher_exchanges():
    history = [USER, use('s1', 'ls', 'student looks'), result('s1'), use('s2', 'cat a.py'), result('s2')]
    t1 = use('t1', 'pytest')
    return [
        {'request': {**BASE, 'messages': history}, 'response_status': 200,
         'response': {'content': t1['content'], 'stop_reason': 'tool_use'}},
        {'request': {**BASE, 'messages': history + [t1, result('t1')]}, 'response_status': 200,
         'response': {'content': [{'type': 'text', 'text': 'Fixed.'}], 'stop_reason': 'end_turn'}},
    ]


def vllm_exchanges():
    def call(call_id, command):
        return {'id': call_id, 'type': 'function',
                'function': {'name': 'run_command', 'arguments': json.dumps({'command': command})}}
    first = {'role': 'assistant', 'content': 'student looks', 'tool_calls': [call('s1', 'ls')],
             'reasoning_content': 'think one', 'reasoning': 'think one'}
    second = {'role': 'assistant', 'content': '', 'tool_calls': [call('s2', 'cat a.py')],
              'reasoning': 'think two'}
    return [
        {'request': {'messages': [{'role': 'system', 'content': 'SYSTEM'}, {'role': 'user', 'content': 'Fix it'}]},
         'response_status': 200, 'response': {'choices': [{'message': first}]}},
        {'request': {'messages': [{'role': 'system', 'content': 'SYSTEM'}, {'role': 'user', 'content': 'Fix it'},
                                  first, {'role': 'tool', 'tool_call_id': 's1', 'content': '{"status":"ok"}'}]},
         'response_status': 200, 'response': {'choices': [{'message': second}]}},
    ]


HANDOFF = {'switch_after': 2, 'agent_calls': 4, 'student_calls': 2, 'teacher_calls': 2,
           'summary_calls_before_handoff': 0, 'student_model': 'openai/gemma4',
           'teacher_model': 'anthropic/claude-haiku-5-5', 'agent_config_sha256': 'abc',
           'sandbox': 'docker', 'swegemma_version': '0.2.11', 'teacher_thinking': 'adaptive'}


def write_run(root: Path, *, resolved=True, handoff=None, teacher=None, name='fastapi_1'):
    task = root / 'oec-x-a1' / 'val' / name
    (task / 'results').mkdir(parents=True)
    (task / 'anthropic_exchanges.json').write_text(json.dumps(teacher or teacher_exchanges()))
    (task / 'vllm_exchanges.json').write_text(json.dumps(vllm_exchanges()))
    (task / 'handoff.json').write_text(json.dumps(handoff or HANDOFF))
    (task / 'results' / f'{name}.json').write_text(json.dumps({'task_id': name, 'resolved': resolved}))
    return task


class ReasoningTests(unittest.TestCase):
    def test_reasoning_is_keyed_by_call_id_from_the_last_request_and_response(self):
        self.assertEqual(oec.student_reasoning_by_call_id(vllm_exchanges()),
                         {'s1': 'think one', 's2': 'think two'})

    def test_failed_student_exchanges_are_skipped(self):
        exchanges = vllm_exchanges()
        exchanges.append({'request': {'messages': []}, 'response_status': 500, 'response': {'error': 'x'}})
        self.assertEqual(set(oec.student_reasoning_by_call_id(exchanges)), {'s1', 's2'})

    def test_no_successful_exchange_gives_nothing(self):
        self.assertEqual(oec.student_reasoning_by_call_id([]), {})


class ConvertTaskTests(unittest.TestCase):
    def test_first_context_prefix_marks_student_turns_context_only(self):
        row = export_sft.convert_task(teacher_exchanges(), first_context_prefix=True)[0]
        student = [m for m in row['messages'] if m['role'] == 'assistant' and m['_turn'] == 0]
        teacher = [m for m in row['messages'] if m['role'] == 'assistant' and m['_turn'] != 0]
        self.assertEqual(len(student), 3)  # commentary, call s1, call s2
        self.assertTrue(all(m['_train'] is False for m in student))
        self.assertTrue(all(m['_train'] is True for m in teacher))

    def test_default_still_rejects_unobserved_assistant_turns(self):
        with self.assertRaisesRegex(ValueError, 'unobserved assistant'):
            export_sft.convert_task(teacher_exchanges())


class BuildTests(unittest.TestCase):
    def test_builds_a_masked_prefix_row_with_student_reasoning(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = write_run(Path(tmp))
            examples = oec.build_oec_examples(task)
        self.assertEqual(len(examples), 1)
        row = examples[0]
        self.assertEqual((row['kind'], row['task_id'], row['mode']), ('oec', 'fastapi_1', 'trajectory'))
        self.assertEqual(row['handoff']['switch_after'], 2)
        assistants = [m for m in row['messages'] if m['role'] == 'assistant']
        self.assertEqual([m['train'] for m in assistants], [False, False, False, True, True])
        reasoning = [m.get('reasoning') for m in assistants[:3]]
        self.assertEqual(reasoning, ['think one', None, 'think two'])  # first message of each student group
        self.assertTrue(all('reasoning' not in m for m in assistants[3:]))

    def test_rejections(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(oec.OecRejected, 'not resolved'):
                oec.build_oec_examples(write_run(root / 'a', resolved=False))
            with self.assertRaisesRegex(oec.OecRejected, 'compaction'):
                oec.build_oec_examples(write_run(root / 'b', handoff={**HANDOFF, 'summary_calls_before_handoff': 1}))
            history = [USER] + [m for i in range(3) for m in (use(f'p{i}', 'pytest'), result(f'p{i}'))]
            loop = [{'request': {**BASE, 'messages': history}, 'response_status': 200,
                     'response': {'content': [{'type': 'text', 'text': 'done'}], 'stop_reason': 'end_turn'}}]
            with self.assertRaisesRegex(oec.OecRejected, 'identical'):
                oec.build_oec_examples(write_run(root / 'c', teacher=loop))


if __name__ == '__main__':
    unittest.main()
