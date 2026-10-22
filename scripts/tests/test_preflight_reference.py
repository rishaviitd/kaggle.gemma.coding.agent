import csv
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    'preflight_reference', Path(__file__).parents[1] / 'runs' / 'preflight_reference.py')
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


def make_wheels(directory: Path, names=('pytest-9.1.1-py3-none-any.whl',)) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        (directory / name).write_bytes(b'')
    return directory


class ChooseWheelDirTests(unittest.TestCase):
    def test_task_cache_with_pytest_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = make_wheels(root / 'task')
            shared = make_wheels(root / 'shared')
            self.assertEqual(preflight.choose_wheel_dir(task, shared), (task, 'task-cache'))

    def test_task_cache_without_pytest_falls_back_to_shared(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = make_wheels(root / 'task', names=('requests-2.0-py3-none-any.whl',))
            shared = make_wheels(root / 'shared')
            self.assertEqual(preflight.choose_wheel_dir(task, shared), (shared, 'shared'))

    def test_missing_task_dir_falls_back_to_shared(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shared = make_wheels(root / 'shared')
            self.assertEqual(preflight.choose_wheel_dir(root / 'absent', shared),
                             (shared, 'shared'))
            self.assertEqual(preflight.choose_wheel_dir(None, shared), (shared, 'shared'))

    def test_no_pytest_anywhere_is_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = make_wheels(root / 'task', names=('six-1.0-py3-none-any.whl',))
            shared = make_wheels(root / 'shared', names=('six-1.0-py3-none-any.whl',))
            self.assertEqual(preflight.choose_wheel_dir(task, shared), (None, 'none'))
            self.assertEqual(preflight.choose_wheel_dir(None, None), (None, 'none'))

    def test_symlinked_task_dir_into_shared_reports_shared(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shared = make_wheels(root / 'shared')
            task = root / 'task'
            task.symlink_to(shared, target_is_directory=True)
            self.assertEqual(preflight.choose_wheel_dir(task, shared), (task, 'shared'))

    def test_task_dir_inside_shared_reports_shared(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shared = make_wheels(root / 'shared')
            nested = make_wheels(shared / 'sub')
            self.assertEqual(preflight.choose_wheel_dir(nested, shared), (nested, 'shared'))

    def test_real_task_dir_with_pytest_is_task_cache_even_with_shared(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shared = make_wheels(root / 'shared')
            task = make_wheels(root / 'task')
            self.assertEqual(preflight.choose_wheel_dir(task, shared), (task, 'task-cache'))

    def test_dangling_symlink_task_dir_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shared = make_wheels(root / 'shared')
            task = root / 'task'
            task.symlink_to(root / 'missing', target_is_directory=True)
            self.assertEqual(preflight.choose_wheel_dir(task, shared), (shared, 'shared'))
            self.assertEqual(preflight.choose_wheel_dir(task, None), (None, 'none'))

    def test_pytest_must_be_a_wheel_name_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = make_wheels(root / 'task', names=('pytest_helpers-1.0.tar.gz',))
            self.assertEqual(preflight.choose_wheel_dir(task, None), (None, 'none'))


class FindManifestRowTests(unittest.TestCase):
    def test_finds_split_and_row_for_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'data' / 'dev').mkdir(parents=True)
            with (root / 'data' / 'dev' / 'manifest.csv').open('w', newline='') as file:
                writer = csv.DictWriter(file, fieldnames=['task_id', 'wheels_dir'])
                writer.writeheader()
                writer.writerow({'task_id': 'fastapi_1', 'wheels_dir': 'data/assets/task_wheels/fastapi_1'})
            split, row = preflight.find_manifest_row('fastapi_1', root / 'data')
            self.assertEqual(split, 'dev')
            self.assertEqual(row['wheels_dir'], 'data/assets/task_wheels/fastapi_1')

    def test_unknown_task_has_no_split(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(preflight.find_manifest_row('nope_1', Path(tmp)), (None, None))


class PlanTaskTests(unittest.TestCase):
    def test_uses_manifest_wheels_dir_when_it_has_pytest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'data' / 'val').mkdir(parents=True)
            with (root / 'data' / 'val' / 'manifest.csv').open('w', newline='') as file:
                writer = csv.DictWriter(file, fieldnames=['task_id', 'wheels_dir'])
                writer.writeheader()
                writer.writerow({'task_id': 't_1', 'wheels_dir': 'data/assets/task_wheels/t_1'})
            make_wheels(root / 'data' / 'assets' / 'task_wheels' / 't_1')
            shared = make_wheels(root / 'shared')
            plan = preflight.plan_task('t_1', shared, root)
            self.assertEqual(plan['wheel_source'], 'task-cache')
            self.assertEqual(plan['split'], 'val')
            self.assertEqual(plan['wheels_dir'], root / 'data/assets/task_wheels/t_1')

    def test_unknown_task_without_shared_is_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = preflight.plan_task('ghost_9', None, Path(tmp))
            self.assertEqual(plan['wheel_source'], 'none')
            self.assertIsNone(plan['wheels_dir'])


class MergeResultsTests(unittest.TestCase):
    def test_merges_pass_fail_timeout_and_none_source(self):
        results = [
            {'task_id': 'a_1', 'passed': True, 'reason': '', 'wheel_source': 'task-cache', 'seconds': 12.5},
            {'task_id': 'b_2', 'passed': False, 'reason': 'not resolved', 'wheel_source': 'shared', 'seconds': 3.0},
            {'task_id': 'c_3', 'passed': False, 'reason': 'timeout', 'wheel_source': 'shared', 'seconds': 600.0},
            {'task_id': 'd_4', 'passed': False, 'reason': 'no wheel cache', 'wheel_source': 'none', 'seconds': 0.0},
        ]
        merged = preflight.merge_results(results)
        self.assertEqual(merged['passed'], ['a_1'])
        self.assertEqual(merged['failed'], {
            'b_2': 'not resolved', 'c_3': 'timeout', 'd_4': 'no wheel cache',
        })
        self.assertEqual(merged['wheel_source'], {
            'a_1': 'task-cache', 'b_2': 'shared', 'c_3': 'shared', 'd_4': 'none',
        })
        self.assertEqual(merged['seconds'], {'a_1': 12.5, 'b_2': 3.0, 'c_3': 600.0, 'd_4': 0.0})

    def test_preserves_task_order_and_truncates_reason(self):
        long_reason = 'x' * 1000
        merged = preflight.merge_results([
            {'task_id': 'z_1', 'passed': True, 'reason': '', 'wheel_source': 'shared', 'seconds': 1.0},
            {'task_id': 'y_2', 'passed': False, 'reason': long_reason, 'wheel_source': 'shared', 'seconds': 2.0},
        ])
        self.assertEqual(merged['passed'], ['z_1'])
        self.assertEqual(list(merged['seconds']), ['z_1', 'y_2'])
        self.assertEqual(len(merged['failed']['y_2']), 300)

    def test_empty_merge(self):
        self.assertEqual(preflight.merge_results([]), {
            'passed': [], 'failed': {}, 'wheel_source': {}, 'seconds': {},
        })


class ParseSingleResultTests(unittest.TestCase):
    def test_parses_valid_result(self):
        text = json.dumps({'task_id': 'a_1', 'passed': False, 'reason': 'boom',
                           'wheel_source': 'shared', 'seconds': 4.25})
        self.assertEqual(preflight.parse_single_result(text), {
            'task_id': 'a_1', 'passed': False, 'reason': 'boom',
            'wheel_source': 'shared', 'seconds': 4.25,
        })

    def test_rejects_missing_keys(self):
        with self.assertRaises(ValueError):
            preflight.parse_single_result(json.dumps({'task_id': 'a_1', 'passed': True}))

    def test_rejects_non_json(self):
        with self.assertRaises(ValueError):
            preflight.parse_single_result('Traceback (most recent call last):')


class RunOneTaskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.shared = make_wheels(self.root / 'shared')

    def tearDown(self):
        self.tmp.cleanup()

    def test_timeout_is_recorded_and_not_raised(self):
        def runner(argv, timeout):
            self.assertEqual(timeout, 600)
            raise subprocess.TimeoutExpired(argv, timeout)

        result = preflight.preflight_task('a_1', self.shared, self.root, runner=runner)
        self.assertEqual(result['passed'], False)
        self.assertEqual(result['reason'], 'timeout')
        self.assertEqual(result['wheel_source'], 'shared')

    def test_crash_without_result_file_is_failure_with_exit_code(self):
        def runner(argv, timeout):
            return subprocess.CompletedProcess(argv, 3, stdout='', stderr='ImportError: boom')

        result = preflight.preflight_task('a_1', self.shared, self.root, runner=runner)
        self.assertFalse(result['passed'])
        self.assertIn('exit 3', result['reason'])
        self.assertIn('ImportError: boom', result['reason'])

    def test_missing_wheels_skips_subprocess(self):
        def runner(argv, timeout):
            raise AssertionError('subprocess must not run without a wheel cache')

        result = preflight.preflight_task('a_1', None, self.root, runner=runner)
        self.assertEqual(result['reason'], 'no wheel cache')
        self.assertEqual(result['wheel_source'], 'none')

    def test_child_result_file_is_parsed(self):
        def runner(argv, timeout):
            out = Path(argv[argv.index('--result-file') + 1])
            out.write_text(json.dumps({'task_id': 'a_1', 'passed': True, 'reason': '',
                                       'wheel_source': 'shared', 'seconds': 1.0}))
            return subprocess.CompletedProcess(argv, 0, stdout='', stderr='')

        result = preflight.preflight_task('a_1', self.shared, self.root, runner=runner)
        self.assertTrue(result['passed'])
        self.assertEqual(result['wheel_source'], 'shared')


class ParseArgsTests(unittest.TestCase):
    def test_parent_mode_arguments(self):
        args = preflight.parse_args([
            '--task-ids', 'a_1', 'b_2', '--concurrency', '4',
            '--wheels-dir-for-all', '/tmp/wheels', '--out', '/tmp/preflight.json',
        ])
        self.assertEqual(args.task_ids, ['a_1', 'b_2'])
        self.assertEqual(args.concurrency, 4)
        self.assertEqual(args.wheels_dir_for_all, Path('/tmp/wheels'))
        self.assertEqual(args.out, Path('/tmp/preflight.json'))
        self.assertIsNone(args.one)

    def test_defaults(self):
        args = preflight.parse_args(['--task-ids', 'a_1', '--out', 'p.json'])
        self.assertEqual(args.concurrency, 2)
        self.assertIsNone(args.wheels_dir_for_all)

    def test_task_ids_and_out_are_required_in_parent_mode(self):
        with self.assertRaises(SystemExit):
            preflight.parse_args(['--out', 'p.json'])
        with self.assertRaises(SystemExit):
            preflight.parse_args(['--task-ids', 'a_1'])

    def test_concurrency_must_be_positive(self):
        with self.assertRaises(SystemExit):
            preflight.parse_args(['--task-ids', 'a_1', '--out', 'p.json', '--concurrency', '0'])

    def test_single_task_mode_arguments(self):
        args = preflight.parse_args([
            '--one', 'a_1', '--split', 'val', '--wheels-dir', '/tmp/w',
            '--wheel-source', 'shared', '--result-file', '/tmp/r.json',
        ])
        self.assertEqual(args.one, 'a_1')
        self.assertEqual(args.split, 'val')
        self.assertEqual(args.wheels_dir, Path('/tmp/w'))
        self.assertEqual(args.wheel_source, 'shared')
        self.assertEqual(args.result_file, Path('/tmp/r.json'))


if __name__ == '__main__':
    unittest.main()
