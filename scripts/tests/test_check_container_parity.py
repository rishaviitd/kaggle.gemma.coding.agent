import json
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, str(Path(__file__).parents[1] / 'runs'))
import check_container_parity as c  # noqa: E402


def fn_event(name: str, response: dict, timestamp: float = 0.0) -> dict:
    return {'author': 'agent', 'timestamp': timestamp,
            'content': {'role': 'user', 'parts': [{'function_response': {
                'name': name, 'response': response}}]}}


class CompareToolResultsTests(unittest.TestCase):
    def test_equal_events_have_no_mismatches(self):
        events = [fn_event('bash', {'stdout': 'x'}), fn_event('read', {'content': 'y'})]
        self.assertEqual(c.compare_tool_results(events, list(events)), [])

    def test_planted_difference_reports_one_mismatch_with_index_and_tool_name(self):
        snapshot = [fn_event('bash', {'stdout': 'x'}), fn_event('read', {'content': 'y'})]
        container = [fn_event('bash', {'stdout': 'x'}), fn_event('read', {'content': 'z'})]
        mismatches = c.compare_tool_results(snapshot, container)
        self.assertEqual(len(mismatches), 1)
        self.assertIn('1', mismatches[0])
        self.assertIn('read', mismatches[0])
        self.assertIn("'y'", mismatches[0])
        self.assertIn("'z'", mismatches[0])

    def test_container_shorter_than_snapshot_is_a_mismatch(self):
        snapshot = [fn_event('bash', {'stdout': 'x'}), fn_event('bash', {'stdout': 'y'})]
        container = [fn_event('bash', {'stdout': 'x'})]
        mismatches = c.compare_tool_results(snapshot, container)
        self.assertEqual(len(mismatches), 1)
        self.assertIn('shorter', mismatches[0])

    def test_container_longer_than_snapshot_is_allowed(self):
        snapshot = [fn_event('bash', {'stdout': 'x'})]
        container = [fn_event('bash', {'stdout': 'x'}), fn_event('bash', {'stdout': 'teacher'})]
        self.assertEqual(c.compare_tool_results(snapshot, container), [])

    def test_top_level_timing_difference_is_ignored(self):
        snapshot = [fn_event('bash', {'stdout': 'x', 'duration': 1.0})]
        container = [fn_event('bash', {'stdout': 'x', 'duration': 9.0})]
        self.assertEqual(c.compare_tool_results(snapshot, container), [])

    def test_nested_timing_difference_is_flagged(self):
        snapshot = [fn_event('bash', {'result': {'duration': 5}})]
        container = [fn_event('bash', {'result': {'duration': 9}})]
        mismatches = c.compare_tool_results(snapshot, container)
        self.assertEqual(len(mismatches), 1)
        self.assertIn('bash', mismatches[0])


class CliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name: str, payload) -> str:
        path = self.dir / name
        path.write_text(payload if isinstance(payload, str) else json.dumps(payload))
        return str(path)

    def run_cli(self, argv):
        out = StringIO()
        with redirect_stdout(out), redirect_stderr(StringIO()):
            code = c.main(argv)
        return code, out.getvalue()

    def test_exit_0_when_all_pairs_pass(self):
        events = [fn_event('bash', {'stdout': 'x'})]
        snap = self.write('s.json', events)
        cont = self.write('c.json', events)
        code, out = self.run_cli([snap, cont])
        self.assertEqual(code, 0)
        self.assertIn(f'PASS {snap} {cont}', out)

    def test_exit_1_on_mismatch(self):
        snap = self.write('s.json', [fn_event('bash', {'stdout': 'x'})])
        cont = self.write('c.json', [fn_event('bash', {'stdout': 'y'})])
        code, out = self.run_cli([snap, cont])
        self.assertEqual(code, 1)
        self.assertIn('bash', out)

    def test_exit_1_when_any_later_pair_fails(self):
        ok = [fn_event('bash', {'stdout': 'x'})]
        bad = [fn_event('bash', {'stdout': 'y'})]
        code, _ = self.run_cli([self.write('s1.json', ok), self.write('c1.json', ok),
                                self.write('s2.json', ok), self.write('c2.json', bad)])
        self.assertEqual(code, 1)

    def test_exit_2_on_unreadable_file(self):
        cont = self.write('c.json', [])
        code, _ = self.run_cli([str(self.dir / 'missing.json'), cont])
        self.assertEqual(code, 2)

    def test_exit_2_on_invalid_json(self):
        snap = self.write('s.json', '{not json')
        cont = self.write('c.json', [])
        code, _ = self.run_cli([snap, cont])
        self.assertEqual(code, 2)

    def test_exit_2_on_non_list_json(self):
        snap = self.write('s.json', {'events': []})
        cont = self.write('c.json', [])
        code, _ = self.run_cli([snap, cont])
        self.assertEqual(code, 2)

    def test_exit_2_on_odd_argument_count(self):
        snap = self.write('s.json', [])
        code, _ = self.run_cli([snap])
        self.assertEqual(code, 2)


if __name__ == '__main__':
    unittest.main()
