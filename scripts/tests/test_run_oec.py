import importlib.util
import json
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack
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
                               api_base=['http://127.0.0.1:8002/v1'], teacher_model='claude-haiku-5-5',
                               teacher_thinking='adaptive', sandbox='docker')
        command = oec.command_for(job, args)
        for flag, value in [('--handoff-after', '5'), ('--handoff-teacher-model', 'claude-haiku-5-5'),
                            ('--run-name', 'oec-x-a1'), ('--task-id', 'a_1'), ('--split', 'train'),
                            ('--sandbox', 'docker'), ('--provider', 'vllm'),
                            ('--api-base', 'http://127.0.0.1:8002/v1')]:
            self.assertEqual(command[command.index(flag) + 1], value)

    def test_command_uses_the_endpoint_given_for_the_job(self):
        args = SimpleNamespace(submission=Path('src/rishav/inspiration-one'), student_model='gemma4',
                               api_base=['http://gpu0/v1', 'http://gpu1/v1'], teacher_model='claude-haiku-5-5',
                               teacher_thinking='adaptive', sandbox='docker')
        leased = oec.command_for(STAGE_JOB, args, 'http://gpu1/v1')
        self.assertEqual(leased[leased.index('--api-base') + 1], 'http://gpu1/v1')
        default = oec.command_for(STAGE_JOB, args)
        self.assertEqual(default[default.index('--api-base') + 1], 'http://gpu0/v1')

    def test_effective_concurrency_is_capped_by_workers_and_pool_size(self):
        self.assertEqual(oec.effective_concurrency(4, ['u0'], 1), 1)
        self.assertEqual(oec.effective_concurrency(4, ['u0', 'u1'], 1), 2)
        self.assertEqual(oec.effective_concurrency(4, ['u0', 'u1'], 3), 4)
        self.assertEqual(oec.effective_concurrency(3, ['u0', 'u1'], 3), 3)

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


STAGE_JOB = {'task_id': 'a_1', 'split': 'train', 'attempt': 1, 'switch_after': 5, 'run_name': 'oec-x-a1'}
STAGE_ARGS = dict(submission=Path('src/rishav/inspiration-one'), student_model='gemma4',
                  api_base=['http://127.0.0.1:8002/v1'], teacher_model='claude-haiku-5-5',
                  teacher_thinking='adaptive', sandbox='docker')


def task_logs(root: Path, job: dict) -> Path:
    return root / 'logs/remote/rishav/inspiration-one' / job['run_name'] / job['split'] / job['task_id']


class StageTests(unittest.TestCase):
    def test_default_stage_command_is_unchanged(self):
        command = oec.command_for(STAGE_JOB, SimpleNamespace(**STAGE_ARGS))
        self.assertIn('--handoff-after', command)
        self.assertNotIn('--handoff-stop-after-switch', command)
        self.assertNotIn('--handoff-resume', command)

    def test_student_stage_stops_after_the_switch(self):
        command = oec.command_for(STAGE_JOB, SimpleNamespace(stage='student', **STAGE_ARGS))
        self.assertIn('--handoff-stop-after-switch', command)
        self.assertNotIn('--handoff-resume', command)
        self.assertEqual(command[command.index('--handoff-after') + 1], '5')
        self.assertEqual(command[command.index('--handoff-teacher-model') + 1], 'claude-haiku-5-5')
        self.assertEqual(command[command.index('--sandbox') + 1], 'docker')

    def test_teacher_stage_resumes_the_snapshot_without_a_fresh_switch_point(self):
        args = SimpleNamespace(stage='teacher', handoff_snapshot=Path('/tmp/snap'), **STAGE_ARGS)
        args.sandbox = 'subprocess'
        command = oec.command_for(STAGE_JOB, args)
        self.assertEqual(command[command.index('--handoff-resume') + 1], '/tmp/snap')
        self.assertNotIn('--handoff-stop-after-switch', command)
        self.assertNotIn('--handoff-after', command)
        self.assertEqual(command[command.index('--handoff-teacher-thinking') + 1], 'adaptive')
        self.assertEqual(command[command.index('--sandbox') + 1], 'subprocess')

    def test_student_job_done_means_the_five_snapshot_files_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = task_logs(root, STAGE_JOB) / 'snapshot'
            self.assertFalse(oec.job_done(root, Path('rishav/inspiration-one'), STAGE_JOB, 'student'))
            snapshot.mkdir(parents=True)
            for name in oec.REQUIRED_FILES[:-1]:
                (snapshot / name).write_text('{}')
            self.assertFalse(oec.job_done(root, Path('rishav/inspiration-one'), STAGE_JOB, 'student'))
            (snapshot / oec.REQUIRED_FILES[-1]).write_text('{}')
            self.assertTrue(oec.job_done(root, Path('rishav/inspiration-one'), STAGE_JOB, 'student'))

    def test_teacher_job_done_needs_exchanges_teacher_calls_and_a_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = task_logs(root, STAGE_JOB)
            result = logs / 'results/a_1.json'
            logs.mkdir(parents=True)
            (logs / 'anthropic_exchanges.json').write_text('[]')
            (logs / 'handoff.json').write_text(json.dumps({'teacher_calls': 0}))
            result.parent.mkdir()
            result.write_text(json.dumps({'resolved': True}))
            self.assertFalse(oec.job_done(root, Path('rishav/inspiration-one'), STAGE_JOB, 'teacher'))
            (logs / 'handoff.json').write_text(json.dumps({'teacher_calls': 2}))
            self.assertTrue(oec.job_done(root, Path('rishav/inspiration-one'), STAGE_JOB, 'teacher'))
            (logs / 'anthropic_exchanges.json').unlink()
            self.assertFalse(oec.job_done(root, Path('rishav/inspiration-one'), STAGE_JOB, 'teacher'))

    def test_stopped_student_job_is_finished_without_a_result_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = task_logs(root, STAGE_JOB) / 'snapshot'
            snapshot.mkdir(parents=True)
            for name in oec.REQUIRED_FILES:
                (snapshot / name).write_text('{}')
            self.assertTrue(oec.job_finished(root, Path('rishav/inspiration-one'), STAGE_JOB, 0, 'student'))
            self.assertFalse(oec.job_finished(root, Path('rishav/inspiration-one'), STAGE_JOB, 0, 'teacher'))
            self.assertTrue(oec.job_finished(root, Path('rishav/inspiration-one'), STAGE_JOB, 1, 'student'))

    def test_stage_validation_requires_one_job_and_a_subprocess_snapshot_resume(self):
        one = [STAGE_JOB]
        with self.assertRaises(ValueError):
            oec.validate_stage(SimpleNamespace(stage='teacher', handoff_snapshot=Path('/s'),
                                               sandbox='subprocess'), [STAGE_JOB, STAGE_JOB])
        with self.assertRaises(ValueError):
            oec.validate_stage(SimpleNamespace(stage='teacher', handoff_snapshot=None,
                                               sandbox='subprocess'), one)
        with self.assertRaises(ValueError):
            oec.validate_stage(SimpleNamespace(stage='teacher', handoff_snapshot=Path('/s'),
                                               sandbox='docker'), one)
        oec.validate_stage(SimpleNamespace(stage='teacher', handoff_snapshot=Path('/s'),
                                           sandbox='subprocess'), one)
        oec.validate_stage(SimpleNamespace(stage='student', handoff_snapshot=None,
                                           sandbox='docker'), [STAGE_JOB, STAGE_JOB])


class EndpointPoolTests(unittest.TestCase):
    def test_pool_holds_each_endpoint_jobs_per_endpoint_times(self):
        pool = oec.EndpointPool(['u0', 'u1'], jobs_per_endpoint=3)
        self.assertEqual(pool.size, 6)
        with ExitStack() as stack:
            held = [stack.enter_context(pool.lease()) for _ in range(6)]
            self.assertEqual(sorted(held), ['u0'] * 3 + ['u1'] * 3)
            self.assertEqual(pool.available, 0)

    def test_endpoint_is_returned_when_the_job_raises(self):
        pool = oec.EndpointPool(['u0'], jobs_per_endpoint=1)
        with self.assertRaises(RuntimeError):
            with pool.lease():
                raise RuntimeError('job failed')
        self.assertEqual(pool.available, 1)
        with pool.lease() as endpoint:
            self.assertEqual(endpoint, 'u0')

    def test_concurrent_jobs_get_distinct_endpoints_with_one_job_each(self):
        pool = oec.EndpointPool(['u0', 'u1', 'u2'], jobs_per_endpoint=1)
        barrier = threading.Barrier(3)
        seen = []
        lock = threading.Lock()

        def job():
            with pool.lease() as endpoint:
                barrier.wait(timeout=5)
                with lock:
                    seen.append(endpoint)

        threads = [threading.Thread(target=job) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertEqual(sorted(seen), ['u0', 'u1', 'u2'])

    def test_leases_never_exceed_pool_size_or_per_endpoint_limit(self):
        pool = oec.EndpointPool(['u0', 'u1'], jobs_per_endpoint=2)
        lock = threading.Lock()
        in_use = {'u0': 0, 'u1': 0}
        worst = {'u0': 0, 'u1': 0}

        def job():
            with pool.lease() as endpoint:
                with lock:
                    in_use[endpoint] += 1
                    worst[endpoint] = max(worst[endpoint], in_use[endpoint])
                time.sleep(0.01)
                with lock:
                    in_use[endpoint] -= 1

        threads = [threading.Thread(target=job) for _ in range(10)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(worst, {'u0': 2, 'u1': 2})
        self.assertEqual(pool.available, 4)


class DefaultConcurrencyTests(unittest.TestCase):
    def test_default_per_endpoint_spreads_workers_across_endpoints(self):
        self.assertEqual(oec.resolve_jobs_per_endpoint(4, ['u0']), 4)
        self.assertEqual(oec.resolve_jobs_per_endpoint(4, ['u0', 'u1']), 2)
        self.assertEqual(oec.resolve_jobs_per_endpoint(4, ['u0', 'u1', 'u2']), 2)
        self.assertEqual(oec.resolve_jobs_per_endpoint(4, ['u0', 'u1', 'u2', 'u3']), 1)

    def test_explicit_per_endpoint_is_kept(self):
        self.assertEqual(oec.resolve_jobs_per_endpoint(4, ['u0', 'u1'], 1), 1)
        self.assertEqual(oec.resolve_jobs_per_endpoint(4, ['u0'], 3), 3)

    def test_default_concurrency_equals_workers(self):
        for workers in range(1, 9):
            for count in range(1, 5):
                endpoints = [f'u{i}' for i in range(count)]
                per = oec.resolve_jobs_per_endpoint(workers, endpoints)
                with self.subTest(workers=workers, endpoints=count):
                    self.assertEqual(oec.effective_concurrency(workers, endpoints, per), workers)

    def test_four_endpoints_four_workers_is_one_job_per_gpu(self):
        endpoints = ['u0', 'u1', 'u2', 'u3']
        pool = oec.EndpointPool(endpoints, oec.resolve_jobs_per_endpoint(4, endpoints))
        self.assertEqual(pool.size, 4)
        with ExitStack() as stack:
            held = sorted(stack.enter_context(pool.lease()) for _ in range(4))
        self.assertEqual(held, endpoints)


class DispatchTests(unittest.TestCase):
    def run_fake(self, endpoints, jobs_per_endpoint, workers, jobs):
        pool = oec.EndpointPool(endpoints, jobs_per_endpoint)
        concurrency = oec.effective_concurrency(workers, endpoints, jobs_per_endpoint)
        lock = threading.Lock()
        in_use = {e: 0 for e in endpoints}
        worst = {e: 0 for e in endpoints}
        completed = []

        def fake_runner(job, api_base):
            with lock:
                in_use[api_base] += 1
                worst[api_base] = max(worst[api_base], in_use[api_base])
            time.sleep(0.02)
            with lock:
                in_use[api_base] -= 1
                completed.append(job['run_name'])
            return 0

        codes = oec.dispatch(jobs, pool, concurrency, fake_runner)
        return codes, worst, completed, pool

    def test_two_endpoints_four_jobs_respect_jobs_per_endpoint(self):
        jobs = [{'run_name': f'r-a{i}'} for i in range(1, 5)]
        for per_endpoint in (1, 2):
            with self.subTest(jobs_per_endpoint=per_endpoint):
                codes, worst, completed, pool = self.run_fake(['u0', 'u1'], per_endpoint, 4, jobs)
                self.assertEqual(codes, [0, 0, 0, 0])
                self.assertEqual(sorted(completed), sorted(j['run_name'] for j in jobs))
                self.assertTrue(all(count <= per_endpoint for count in worst.values()))
                self.assertEqual(pool.available, 2 * per_endpoint)


if __name__ == '__main__':
    unittest.main()
