import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

spec = importlib.util.spec_from_file_location('pass3', Path(__file__).parents[1] / 'runs' / 'run_pass3.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class Pass3Tests(unittest.TestCase):
    def test_starts_three_processes_before_waiting(self):
        events = []
        def start(*args, **kwargs):
            events.append('start')
            child = MagicMock()
            child.wait.side_effect = lambda **kw: events.append('wait') or 0
            child.poll.return_value = 0
            return child
        with tempfile.TemporaryDirectory() as tmp, patch.object(runner.subprocess, 'Popen', side_effect=start):
            runner.run_attempts([(['python', 'task'], Path(tmp) / f'{i}.log') for i in range(3)])
        self.assertEqual(events[:4], ['start', 'start', 'start', 'wait'])

    def test_results_and_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            submission = root / 'src/rishav/inspiration-one'
            submission.mkdir(parents=True)
            (submission / 'agent.yaml').write_text('name: agent')
            data = root / 'data/val'
            data.mkdir(parents=True)
            (data / 'tasks.jsonl').write_text('{"instance_id":"task1"}\n')
            commands = []
            def run(jobs):
                for command, log in jobs:
                    commands.append(command)
                    name = command[command.index('--run-name') + 1]
                    destination = root / 'logs/remote/rishav/inspiration-one' / name / 'val/task1/results/task1.json'
                    destination.parent.mkdir(parents=True)
                    destination.write_text(json.dumps({'resolved': name.endswith('2')}))
            argv = ['run_pass3.py', '--submission', str(submission), '--provider', 'anthropic',
                    '--model', 'claude-haiku-5-5', '--run-name', 'example', '--task-ids', 'task1']
            with patch.object(runner, 'ROOT', root), patch.object(runner.sys, 'argv', argv), patch.object(runner, 'run_attempts', side_effect=run):
                runner.main()
                self.assertEqual(len(commands), 3)
                for command in commands:
                    self.assertNotIn('--max-tool-calls', command)
                with patch.object(runner.sys, 'argv', argv + ['--resume']):
                    runner.main()
                self.assertEqual(len(commands), 3)
            report = json.loads((root / 'logs/remote/rishav/inspiration-one/example/summary.json').read_text())
            self.assertEqual(report['solved'], 1)
            self.assertEqual(report['pass_at_3'], 1)
            self.assertTrue(report['complete'])
            self.assertEqual(report['tasks']['task1']['attempts'], ['failed', 'passed', 'failed'])

    def test_two_new_attempts_include_baseline_without_modifying_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            submission = root / 'src/rishav/inspiration-one'
            submission.mkdir(parents=True)
            (submission / 'agent.yaml').write_text('name: agent')
            data = root / 'data/val'
            data.mkdir(parents=True)
            (data / 'tasks.jsonl').write_text('{"instance_id":"task1"}\n')
            baseline = root / 'logs/remote/rishav/inspiration-one/prior/val/task1/results/task1.json'
            baseline.parent.mkdir(parents=True)
            original = '{"resolved":true}'
            baseline.write_text(original)
            argv = ['run_pass3.py', '--submission', str(submission), '--provider', 'anthropic',
                    '--model', 'claude-haiku-5-5', '--run-name', 'next', '--task-ids', 'task1',
                    '--parallel', '2', '--baseline-run', 'prior']
            with patch.object(runner, 'ROOT', root), patch.object(runner.sys, 'argv', argv), patch.object(runner, 'run_attempts') as run:
                runner.main()
            self.assertEqual(len(run.call_args.args[0]), 2)
            self.assertEqual(baseline.read_text(), original)
            report = json.loads((baseline.parents[4] / 'next/summary.json').read_text())
            self.assertEqual(report['attempts_per_task'], 3)
            self.assertEqual(report['tasks']['task1']['attempts'], ['passed', 'missing', 'missing'])
            self.assertEqual(report['pass_at_3'], 1)
            self.assertFalse(report['complete'])

    def test_error_is_not_a_valid_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'result.json'
            self.assertEqual(runner.read_result(path), 'missing')
            path.write_text('{"resolved":false,"error_message":"API failed"}')
            self.assertEqual(runner.read_result(path), 'error')

    def test_interruption_stops_all_children(self):
        children = [MagicMock() for _ in range(3)]
        for child in children:
            child.poll.return_value = None
        children[0].wait.side_effect = [KeyboardInterrupt(), 0]
        with tempfile.TemporaryDirectory() as tmp, patch.object(runner.subprocess, 'Popen', side_effect=children):
            with self.assertRaises(KeyboardInterrupt):
                runner.run_attempts([(['python', 'task'], Path(tmp) / f'{i}.log') for i in range(3)])
        for child in children:
            child.terminate.assert_called_once()


if __name__ == '__main__':
    unittest.main()
