import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import audit_parity as audit  # noqa: E402
import merge_datasets as merge  # noqa: E402

TOOLS = [{'type': 'function', 'function': {'name': 'run_command', 'description': 'x',
                                           'parameters': {'type': 'object', 'properties': {}}}}]


def call(call_id='c1'):
    return {'id': call_id, 'type': 'function', 'function': {'name': 'run_command', 'arguments': {'command': 'ls'}}}


def row(kind, task='t1', system='SYS', tools=TOOLS, student_trains=False):
    messages = [{'role': 'system', 'content': system}, {'role': 'user', 'content': 'u'}]
    if kind == 'oec':
        messages += [{'role': 'assistant', 'content': '', 'tool_calls': [call('s')], 'reasoning': 'r',
                      'train': student_trains},
                     {'role': 'tool', 'content': '{}', 'tool_call_id': 's', 'name': 'run_command'}]
    messages += [{'role': 'assistant', 'content': '', 'tool_calls': [call('t')], 'reasoning': 'r', 'train': True},
                 {'role': 'tool', 'content': '{}', 'tool_call_id': 't', 'name': 'run_command'},
                 {'role': 'assistant', 'content': 'done', 'train': True}]
    return {'task_id': task, 'kind': kind, 'mode': 'trajectory', 'enable_thinking': True,
            'tools': tools, 'messages': messages}


class SelectTests(unittest.TestCase):
    def test_round_robin_across_tasks_and_deterministic(self):
        rows = [row('oec', task=f't{i % 3}') | {'n': i} for i in range(9)]
        picked = merge.select_balanced(rows, 3, seed=1)
        self.assertEqual(sorted(r['task_id'] for r in picked), ['t0', 't1', 't2'])
        self.assertEqual(picked, merge.select_balanced(rows, 3, seed=1))

    def test_count_larger_than_pool_returns_everything(self):
        self.assertEqual(len(merge.select_balanced([row('oec')] * 2, 10, seed=1)), 2)


class MergeTests(unittest.TestCase):
    def test_share_manifest_and_kinds(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for name, kind, n in [('clean', 'clean', 6), ('oec', 'oec', 10)]:
                d = base / name
                d.mkdir()
                (d / 'train.jsonl').write_text(''.join(json.dumps(row(kind, task=f't{i}')) + '\n' for i in range(n)))
                extra = {'oec': {'sandbox': 'docker'}} if kind == 'oec' else {'thoughts': {'steps': 1}}
                (d / 'manifest.json').write_text(json.dumps({'schema_version': 'gemma4-sft-v6',
                                                             'examples': n, 'thoughts': {'steps': 1}, **extra}))
            manifest = merge.merge(base / 'clean', base / 'oec', base / 'out', oec_share=1 / 3, seed=1)
            rows = [json.loads(l) for l in (base / 'out/train.jsonl').read_text().splitlines()]
            self.assertEqual(manifest['sources'], {'clean': 6, 'oec': 3})
            self.assertEqual(len(rows), 9)
            self.assertEqual(manifest['examples'], 9)
            self.assertEqual(sum(r['kind'] == 'oec' for r in rows), 3)
            self.assertEqual(manifest['schema_version'], 'gemma4-sft-v6')
            self.assertEqual(manifest['oec'], {'sandbox': 'docker'})


class AuditTests(unittest.TestCase):
    def test_consistent_rows_pass(self):
        self.assertEqual(audit.audit([row('clean'), row('oec')]), [])

    def test_system_prompt_mismatch(self):
        problems = audit.audit([row('clean'), row('oec', system='OTHER')])
        self.assertTrue(any('system prompt' in p for p in problems))

    def test_tools_mismatch(self):
        other = [{'type': 'function', 'function': {'name': 'read_file', 'description': 'x', 'parameters': {}}}]
        self.assertTrue(any('tools' in p for p in audit.audit([row('clean'), row('oec', tools=other)])))

    def test_oec_row_must_have_masked_student_and_trained_teacher(self):
        self.assertTrue(any('student' in p for p in audit.audit([row('oec', student_trains=True)])))

    def test_missing_reasoning_on_a_tool_call_step(self):
        bad = row('clean')
        del bad['messages'][2]['reasoning']
        self.assertTrue(any('reasoning' in p for p in audit.audit([bad])))

    def test_string_arguments_are_a_serialization_mismatch(self):
        bad = row('clean')
        bad['messages'][2]['tool_calls'][0]['function']['arguments'] = '{"command": "ls"}'
        self.assertTrue(any('arguments' in p for p in audit.audit([bad])))

    def test_unknown_tool_name(self):
        bad = row('clean')
        bad['messages'][2]['tool_calls'][0]['function']['name'] = 'nope'
        self.assertTrue(any('not in tools' in p for p in audit.audit([bad])))


if __name__ == '__main__':
    unittest.main()
