import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location('run_oec', Path(__file__).parents[1] / 'runs' / 'run_oec.py')
oec = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oec)


def fake_repo(root: Path):
    (root / 'docs/reports').mkdir(parents=True)
    (root / 'docs/reports/reference_patch_passed_task_ids.txt').write_text('a_1\nb_2\n')
    for split, ids in {'train': ['a_1'], 'dev': ['b_2'], 'val': ['c_3']}.items():
        (root / 'data' / split).mkdir(parents=True)
        (root / 'data' / split / 'manifest.csv').write_text('task_id,split\n' + ''.join(f'{i},{split}\n' for i in ids))


class PlanTests(unittest.TestCase):
    def test_verified_tasks_map_to_their_split_and_skip_unverified(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_repo(Path(tmp))
            self.assertEqual(oec.verified_tasks(Path(tmp)), {'a_1': 'train', 'b_2': 'dev'})

    def test_unknown_verified_task_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_repo(Path(tmp))
            (Path(tmp) / 'docs/reports/reference_patch_passed_task_ids.txt').write_text('zzz_9\n')
            with self.assertRaises(ValueError):
                oec.verified_tasks(Path(tmp))

    def test_plan_has_one_job_per_attempt_with_stratified_points(self):
        jobs = oec.plan_runs({'a_1': 'train'}, attempts=3, low=1, high=12, seed=7, run_prefix='oec-x')
        self.assertEqual([j['attempt'] for j in jobs], [1, 2, 3])
        self.assertEqual([j['run_name'] for j in jobs], ['oec-x-a1', 'oec-x-a2', 'oec-x-a3'])
        self.assertTrue(1 <= jobs[0]['switch_after'] <= 4 and 9 <= jobs[2]['switch_after'] <= 12)
        self.assertEqual(jobs, oec.plan_runs({'a_1': 'train'}, 3, 1, 12, 7, 'oec-x'))

    def test_command_passes_the_handoff_flags(self):
        job = {'task_id': 'a_1', 'split': 'train', 'attempt': 1, 'switch_after': 5, 'run_name': 'oec-x-a1'}
        args = SimpleNamespace(submission=Path('src/rishav/inspiration-one'), student_model='gemma4',
                               api_base='http://127.0.0.1:8002/v1', teacher_model='claude-haiku-5-5',
                               teacher_thinking='adaptive', sandbox='docker')
        command = oec.command_for(job, args)
        for flag, value in [('--handoff-after', '5'), ('--handoff-teacher-model', 'claude-haiku-5-5'),
                            ('--run-name', 'oec-x-a1'), ('--task-id', 'a_1'), ('--split', 'train'),
                            ('--sandbox', 'docker'), ('--provider', 'vllm')]:
            self.assertEqual(command[command.index(flag) + 1], value)

    def test_job_done_needs_a_boolean_result_without_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = {'task_id': 'a_1', 'split': 'train', 'run_name': 'r-a1'}
            result = root / 'logs/remote/rishav/inspiration-one/r-a1/train/a_1/results/a_1.json'
            self.assertFalse(oec.job_done(root, Path('rishav/inspiration-one'), job))
            result.parent.mkdir(parents=True)
            result.write_text(json.dumps({'resolved': False, 'error': 'boom'}))
            self.assertFalse(oec.job_done(root, Path('rishav/inspiration-one'), job))
            result.write_text(json.dumps({'resolved': False}))
            self.assertTrue(oec.job_done(root, Path('rishav/inspiration-one'), job))


if __name__ == '__main__':
    unittest.main()
