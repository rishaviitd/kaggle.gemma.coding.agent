import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / 'runs'))
sys.path.insert(0, str(Path(__file__).parents[1] / 'sft_export'))
import eval_metrics as m  # noqa: E402


def call(call_id, command='ls'):
    return {'id': call_id, 'type': 'function', 'function': {
        'name': 'run_command', 'arguments': json.dumps({'command': command})}}


def write_run(root: Path, task: str, resolved: bool, steps):
    """steps: list of (command, ok) pairs; the last assistant message is the response."""
    directory = root / task
    (directory / 'results').mkdir(parents=True)
    (directory / 'results' / f'{task}.json').write_text(json.dumps({'task_id': task, 'resolved': resolved}))
    messages = [{'role': 'system', 'content': 's'}, {'role': 'user', 'content': 'u'}]
    for index, (command, ok) in enumerate(steps[:-1]):
        messages.append({'role': 'assistant', 'tool_calls': [call(f'c{index}', command)]})
        body = '{"status":"ok"}' if ok else '{"status":"error"}'
        messages.append({'role': 'tool', 'tool_call_id': f'c{index}', 'content': body})
    last_command, _ = steps[-1]
    final = {'role': 'assistant', 'content': '', 'tool_calls': [call('last', last_command)]}
    (directory / 'vllm_exchanges.json').write_text(json.dumps([{
        'request': {'messages': messages}, 'response_status': 200,
        'response': {'choices': [{'message': final}]}}]))


class MetricsTests(unittest.TestCase):
    def test_run_metrics_counts_calls_failures_and_loops(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_run(root, 'a', True, [('ls', True), ('pytest', False), ('pytest -x', True), ('cat x', True)])
            write_run(root, 'b', False, [('p', True), ('p', True), ('p', True)])
            a = m.run_metrics(root / 'a')
            b = m.run_metrics(root / 'b')
        self.assertEqual((a['resolved'], a['tool_calls'], a['failed_results'], a['loop']), (True, 4, 1, False))
        self.assertTrue(b['loop'])

    def test_summary(self):
        runs = [
            {'resolved': True, 'tool_calls': 4, 'failed_results': 1, 'loop': False},
            {'resolved': False, 'tool_calls': 6, 'failed_results': 0, 'loop': True},
            {'resolved': True, 'tool_calls': 2, 'failed_results': 0, 'loop': False},
            {'resolved': False, 'tool_calls': 8, 'failed_results': 2, 'loop': False},
        ]
        s = m.summarize(runs)
        self.assertEqual(s['runs'], 4)
        self.assertAlmostEqual(s['pass_at_1'], 0.5)
        self.assertAlmostEqual(s['loop_rate'], 0.25)
        self.assertAlmostEqual(s['tool_call_validity'], 1 - 3 / 20)
        self.assertAlmostEqual(s['steps_per_resolved'], 3.0)
        self.assertAlmostEqual(s['recovery_rate'], 0.5)  # 1 of the 2 runs with a failed result resolved
        self.assertEqual(s['recovery_runs'], 2)

    def test_pass_at_k_over_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            r1, r2 = Path(tmp) / 'r1', Path(tmp) / 'r2'
            write_run(r1, 'a', False, [('ls', True)])
            write_run(r2, 'a', True, [('ls', True)])
            write_run(r1, 'b', False, [('ls', True)])
            write_run(r2, 'b', False, [('ls', True)])
            self.assertEqual(m.pass_at_k([r1, r2]), {'tasks': 2, 'passed': 1})


if __name__ == '__main__':
    unittest.main()
