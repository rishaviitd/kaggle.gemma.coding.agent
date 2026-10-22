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
                      'train': student_trains, 'source': 'student'},
                     {'role': 'tool', 'content': '{}', 'tool_call_id': 's', 'name': 'run_command'}]
    messages += [{'role': 'assistant', 'content': '', 'tool_calls': [call('t')], 'reasoning': 'r', 'train': True,
                  **({'source': 'teacher'} if kind == 'oec' else {})},
                 {'role': 'tool', 'content': '{}', 'tool_call_id': 't', 'name': 'run_command'},
                 {'role': 'assistant', 'content': 'done', 'train': True,
                  **({'source': 'teacher'} if kind == 'oec' else {})}]
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

    def _dirs(self, base, oec_manifest, clean_extra=None, oec_rows=None):
        for name, kind, n in [('clean', 'clean', 6), ('oec', 'oec', 10)]:
            d = base / name
            d.mkdir()
            rows = oec_rows if (kind == 'oec' and oec_rows is not None) else [row(kind, task=f't{i}') for i in range(n)]
            (d / 'train.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
            manifest = oec_manifest if kind == 'oec' else {'schema_version': 'gemma4-sft-v6', 'thoughts': {},
                                                           **(clean_extra or {})}
            (d / 'manifest.json').write_text(json.dumps(manifest))

    def test_sandbox_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self._dirs(base, {'schema_version': 'gemma4-sft-v6', 'thoughts': {}, 'oec': {'sandbox': 'local'}})
            with self.assertRaisesRegex(ValueError, 'sandbox mismatch'):
                merge.merge(base / 'clean', base / 'oec', base / 'out', oec_share=1 / 3, seed=1)

    def test_schema_and_thoughts_are_required(self):
        for manifest in [{'schema_version': 'gemma4-sft-v5', 'thoughts': {}, 'oec': {'sandbox': 'docker'}},
                         {'schema_version': 'gemma4-sft-v6', 'oec': {'sandbox': 'docker'}}]:
            with tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                self._dirs(base, manifest)
                with self.assertRaisesRegex(ValueError, 'gemma4-sft-v6'):
                    merge.merge(base / 'clean', base / 'oec', base / 'out', oec_share=1 / 3, seed=1)

    def test_oec_row_with_another_kind_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self._dirs(base, {'schema_version': 'gemma4-sft-v6', 'thoughts': {}, 'oec': {'sandbox': 'docker'}},
                       oec_rows=[row('clean')])
            with self.assertRaisesRegex(ValueError, 'kind'):
                merge.merge(base / 'clean', base / 'oec', base / 'out', oec_share=1 / 3, seed=1)


class AuditTests(unittest.TestCase):
    def test_consistent_rows_pass(self):
        self.assertEqual(audit.audit([row('clean'), row('oec')]), [])

    def test_system_prompt_mismatch(self):
        problems = audit.audit([row('clean'), row('oec', system='OTHER')])
        self.assertTrue(any('system prompt' in p for p in problems))

    def test_tools_mismatch(self):
        other = [{'type': 'function', 'function': {'name': 'read_file', 'description': 'x', 'parameters': {}}}]
        self.assertTrue(any('tools' in p for p in audit.audit([row('clean'), row('oec', tools=other)])))

    def test_trained_student_turn_fails(self):
        problems = audit.audit([row('oec', student_trains=True)])
        self.assertTrue(any('student turn is trained' in p for p in problems))

    def test_masked_teacher_step_after_trained_teacher_step_passes(self):
        # export_sft.mask_unsound_steps masks failed/repeated teacher steps anywhere in the row
        ok = row('oec')
        ok['messages'].insert(-1, {'role': 'assistant', 'content': 'retry', 'train': False, 'source': 'teacher'})
        self.assertEqual(audit.audit([ok]), [])

    def test_oec_row_needs_a_trained_teacher_turn(self):
        bad = row('oec')
        for m in bad['messages']:
            if m.get('source') == 'teacher':
                m['train'] = False
        self.assertTrue(any('no trained teacher' in p for p in audit.audit([bad])))

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
