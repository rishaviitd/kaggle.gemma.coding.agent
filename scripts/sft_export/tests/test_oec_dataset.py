import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
from test_export_oec import HANDOFF, write_run  # noqa: E402
import build_oec_dataset as builder  # noqa: E402

spec = importlib.util.spec_from_file_location('rationalize', HERE.parent / 'rationalize.py')
rationalize = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rationalize)


class BuilderTests(unittest.TestCase):
    def test_builds_rows_manifest_and_rejection_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'logs'
            write_run(root / 'a', name='fastapi_1')
            write_run(root / 'b', name='fastapi_2', resolved=False)
            out = Path(tmp) / 'out'
            manifest = builder.build([root], out)
            rows = [json.loads(l) for l in (out / 'trajectory/train.jsonl').read_text().splitlines()]
            self.assertEqual([r['task_id'] for r in rows], ['fastapi_1'])
            self.assertEqual(manifest['examples'], 1)
            self.assertEqual(manifest['oec']['runs_used'], 1)
            self.assertEqual(manifest['oec']['rejected'][0]['reason'], 'not resolved')
            self.assertEqual(manifest['oec']['sandbox'], 'docker')
            saved = json.loads((out / 'trajectory/manifest.json').read_text())
            self.assertEqual(saved['schema_version'], 'gemma4-sft-v5')

    def test_mixed_config_hashes_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'logs'
            write_run(root / 'a', name='fastapi_1')
            write_run(root / 'b', name='fastapi_2', handoff={**HANDOFF, 'agent_config_sha256': 'different'})
            with self.assertRaisesRegex(ValueError, 'agent_config_sha256'):
                builder.build([root], Path(tmp) / 'out')

    def test_no_usable_runs_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'logs'
            write_run(root / 'a', resolved=False)
            with self.assertRaisesRegex(ValueError, 'No usable'):
                builder.build([root], Path(tmp) / 'out')


class RationalizeTests(unittest.TestCase):
    def test_steps_with_native_reasoning_are_not_rationalized(self):
        call = {'id': 'c', 'type': 'function', 'function': {'name': 'run_command', 'arguments': {'command': 'ls'}}}
        rows = [{'messages': [
            {'role': 'system', 'content': 's'}, {'role': 'user', 'content': 'u'},
            {'role': 'assistant', 'content': '', 'tool_calls': [call], 'reasoning': 'native'},
            {'role': 'tool', 'content': '{}', 'tool_call_id': 'c', 'name': 'run_command'},
            {'role': 'assistant', 'content': '', 'tool_calls': [call]},
            {'role': 'tool', 'content': '{}', 'tool_call_id': 'c', 'name': 'run_command'},
        ]}]
        jobs = rationalize.build_jobs(rows)
        self.assertEqual([j['msg'] for j in jobs], [4])


class ApplyGuardTests(unittest.TestCase):
    def test_apply_refuses_existing_output_and_the_input_file(self):
        import argparse
        with tempfile.TemporaryDirectory() as tmp:
            data, out = Path(tmp) / 'in.jsonl', Path(tmp) / 'out.jsonl'
            data.write_text('')
            out.write_text('keep')
            for target in (out, data):
                args = argparse.Namespace(data=str(data), out=str(target), prompt_version='v5')
                with self.assertRaises(SystemExit):
                    rationalize.apply(args)
            self.assertEqual(out.read_text(), 'keep')


if __name__ == '__main__':
    unittest.main()
