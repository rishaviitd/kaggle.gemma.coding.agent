"""Plan and run student-to-Haiku handoff attempts for the OEC dataset."""
import argparse
import csv
import json
import math
import queue
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from handoff_llm import draw_switch_points
from handoff_snapshot import REQUIRED_FILES

ROOT = Path(__file__).resolve().parents[2]
SPLITS = ('train', 'dev', 'val')


def verified_tasks(root: Path) -> dict[str, str]:
    """Task id -> split for every task whose reference patch passes the verifier."""
    passed = [line.strip() for line in
              (root / 'docs/reports/reference_patch_passed_task_ids.txt').read_text().splitlines()
              if line.strip()]
    split_of = {}
    for split in SPLITS:
        with (root / 'data' / split / 'manifest.csv').open(newline='', encoding='utf-8') as file:
            for row in csv.DictReader(file):
                split_of[row['task_id']] = split
    missing = [task for task in passed if task not in split_of]
    if missing:
        raise ValueError('verified tasks missing from the split manifests: ' + ', '.join(missing))
    return {task: split_of[task] for task in passed}


def plan_runs(tasks: dict[str, str], attempts: int, low: int, high: int, seed: int,
              run_prefix: str) -> list[dict]:
    jobs = []
    for task_id, split in sorted(tasks.items()):
        points = draw_switch_points(attempts, low, high, f'{seed}:{task_id}')
        for attempt, switch_after in enumerate(points, start=1):
            jobs.append({'task_id': task_id, 'split': split, 'attempt': attempt,
                         'switch_after': switch_after, 'run_name': f'{run_prefix}-a{attempt}'})
    return jobs


class EndpointPool:
    """Thread-safe lease of student endpoints. Each endpoint is listed jobs_per_endpoint times,
    so it serves at most that many jobs at once; a leased endpoint goes back when its job exits."""

    def __init__(self, endpoints: list[str], jobs_per_endpoint: int = 1):
        self._free: queue.Queue = queue.Queue()
        for endpoint in endpoints:
            for _ in range(jobs_per_endpoint):
                self._free.put(endpoint)
        self.size = len(endpoints) * jobs_per_endpoint

    @property
    def available(self) -> int:
        return self._free.qsize()

    @contextmanager
    def lease(self):
        endpoint = self._free.get()
        try:
            yield endpoint
        finally:
            self._free.put(endpoint)


def resolve_jobs_per_endpoint(workers: int, endpoints: list[str], jobs_per_endpoint: int | None = None) -> int:
    """Explicit value, or by default enough per endpoint that the pool covers --workers."""
    if jobs_per_endpoint is not None:
        return jobs_per_endpoint
    return math.ceil(workers / len(endpoints))


def effective_concurrency(workers: int, endpoints: list[str], jobs_per_endpoint: int) -> int:
    return min(workers, len(endpoints) * jobs_per_endpoint)


def dispatch(jobs: list[dict], pool: EndpointPool, concurrency: int, run_job) -> list:
    """Run each job on a leased endpoint via run_job(job, api_base); exit codes come back in job order."""
    def leased(job):
        with pool.lease() as api_base:
            return run_job(job, api_base)

    with ThreadPoolExecutor(concurrency) as executor:
        return list(executor.map(leased, jobs))


def command_for(job: dict, args, api_base: str | None = None) -> list[str]:
    """Pipeline command for one job. stage None is the original full run (student and teacher together).
    api_base is the endpoint leased for this job; it defaults to the first --api-base."""
    stage = getattr(args, 'stage', None)
    if api_base is None:
        api_base = args.api_base[0]
    command = [sys.executable, 'scripts/runs/task_pipeline.py',
               '--split', job['split'], '--task-id', job['task_id'],
               '--submission', str(args.submission), '--run-name', job['run_name'],
               '--provider', 'vllm', '--model', args.student_model, '--api-base', api_base,
               '--handoff-teacher-model', args.teacher_model,
               '--handoff-teacher-thinking', args.teacher_thinking]
    if stage == 'teacher':
        # The switch point comes from the snapshot; a freshly drawn value would fail its cross-check.
        command += ['--handoff-resume', str(args.handoff_snapshot)]
    else:
        command += ['--handoff-after', str(job['switch_after'])]
        if stage == 'student':
            command.append('--handoff-stop-after-switch')
    temperature = getattr(args, 'temperature', None)
    if temperature is not None:
        # Student and teacher must share the sampling config, or agent_config_sha256 differs.
        command += ['--temperature', str(temperature)]
    return command + ['--sandbox', args.sandbox, '--skip-langfuse']


def job_logs(root: Path, submission_rel: Path, job: dict) -> Path:
    return root / 'logs/remote' / submission_rel / job['run_name'] / job['split'] / job['task_id']


def result_ok(path: Path) -> bool:
    try:
        result = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(result.get('resolved'), bool) and not result.get('error')


def teacher_trace_done(logs: Path) -> bool:
    try:
        handoff = json.loads((logs / 'handoff.json').read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return False
    calls = handoff.get('teacher_calls') if isinstance(handoff, dict) else None
    return ((logs / 'anthropic_exchanges.json').is_file()
            and isinstance(calls, int) and not isinstance(calls, bool) and calls >= 1)


def job_done(root: Path, submission_rel: Path, job: dict, stage: str | None = None) -> bool:
    logs = job_logs(root, submission_rel, job)
    if stage == 'student':
        # A stopped student run writes no result file; its snapshot is the finished product.
        return all((logs / 'snapshot' / name).is_file() for name in REQUIRED_FILES)
    if stage == 'teacher' and not teacher_trace_done(logs):
        return False
    return result_ok(logs / 'results' / f"{job['task_id']}.json")


def job_finished(root: Path, submission_rel: Path, job: dict, code: int, stage: str | None = None) -> bool:
    """Whether a finished process produced what its stage needs. Stopped student runs may exit
    with any code; the pipeline also reports its own failures by result file, not exit code."""
    if stage is None:
        return code == 0
    return job_done(root, submission_rel, job, stage)


def validate_stage(args, jobs: list[dict]) -> None:
    if args.stage == 'teacher':
        if args.handoff_snapshot is None:
            raise ValueError('--stage teacher needs --handoff-snapshot')
        if args.sandbox != 'subprocess':
            raise ValueError('--stage teacher needs --sandbox subprocess (the snapshot is subprocess-only)')
        if len(jobs) != 1:
            raise ValueError(f'--stage teacher needs exactly one planned job, got {len(jobs)}; '
                             'pass one --task-ids entry and --attempts 1')
    elif args.handoff_snapshot is not None:
        raise ValueError('--handoff-snapshot only applies to --stage teacher')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-prefix', required=True, help='Run name prefix, e.g. oec-v1; attempts become <prefix>-a1...')
    p.add_argument('--api-base', required=True, nargs='+',
                   help='Student vLLM base(s), one per GPU, e.g. http://127.0.0.1:8002/v1 http://127.0.0.1:8003/v1')
    p.add_argument('--jobs-per-endpoint', type=int, default=None,
                   help='Max concurrent jobs per student endpoint. Default: ceil(--workers / number of endpoints), '
                        'so the default concurrency equals --workers.')
    p.add_argument('--submission', type=Path, default=ROOT / 'src/rishav/inspiration-one')
    p.add_argument('--student-model', default='gemma4')
    p.add_argument('--teacher-model', default='claude-haiku-5-5')
    p.add_argument('--teacher-thinking', choices=('adaptive', 'off'), default='adaptive')
    p.add_argument('--sandbox', choices=('docker', 'subprocess'), default='docker')
    p.add_argument('--attempts', type=int, default=3)
    p.add_argument('--switch-low', type=int, default=2)
    p.add_argument('--switch-high', type=int, default=16)
    p.add_argument('--seed', type=int, default=1)
    p.add_argument('--workers', type=int, default=4)
    p.add_argument('--task-ids', nargs='+', help='Restrict to these verified task ids')
    p.add_argument('--stage', choices=('student', 'teacher'),
                   help='student: stop at the switch and write the snapshot. teacher: resume '
                        '--handoff-snapshot and finish with Haiku. Default: the full run.')
    p.add_argument('--handoff-snapshot', type=Path,
                   help='Snapshot dir from a student stage run (teacher stage only; one task)')
    p.add_argument('--temperature', type=float, default=None,
                   help='Forwarded to every stage; student and teacher must use the same value')
    p.add_argument('--dry-run', action='store_true', help='Print the plan and exit')
    args = p.parse_args()

    tasks = verified_tasks(ROOT)
    if args.task_ids:
        wanted = [t for item in args.task_ids for t in item.split(',') if t]
        unknown = [t for t in wanted if t not in tasks]
        if unknown:
            p.error('not verified tasks: ' + ', '.join(unknown))
        tasks = {t: tasks[t] for t in wanted}
    submission_rel = args.submission.resolve().relative_to((ROOT / 'src').resolve())
    jobs = plan_runs(tasks, args.attempts, args.switch_low, args.switch_high, args.seed, args.run_prefix)
    try:
        validate_stage(args, jobs)
    except ValueError as error:
        p.error(str(error))
    if args.jobs_per_endpoint is not None and args.jobs_per_endpoint < 1:
        p.error('--jobs-per-endpoint must be at least 1')
    per_endpoint = resolve_jobs_per_endpoint(args.workers, args.api_base, args.jobs_per_endpoint)
    pool = EndpointPool(args.api_base, per_endpoint)
    concurrency = effective_concurrency(args.workers, args.api_base, per_endpoint)
    todo = [job for job in jobs if not job_done(ROOT, submission_rel, job, args.stage)]
    print(f'{len(tasks)} verified tasks x {args.attempts} attempts', flush=True)
    print(f'{len(jobs)} planned, {len(jobs) - len(todo)} already done, {len(todo)} to run, '
          f'concurrency {concurrency}', flush=True)
    if args.dry_run:
        for job in todo:
            print(json.dumps(job))
        return
    log_dir = ROOT / 'logs/oec_runs' / args.run_prefix
    log_dir.mkdir(parents=True, exist_ok=True)

    def run(job, api_base):
        log = log_dir / f"{job['run_name']}-{job['task_id']}.log"
        with log.open('w') as output:
            code = subprocess.run(command_for(job, args, api_base), cwd=ROOT, stdout=output,
                                  stderr=subprocess.STDOUT).returncode
        print(f"{job['run_name']} {job['task_id']} k={job['switch_after']} exit={code}", flush=True)
        return code

    codes = dispatch(todo, pool, concurrency, run)
    failed = sum(not job_finished(ROOT, submission_rel, job, code, args.stage)
                 for job, code in zip(todo, codes))
    print(f'finished; {failed} process failure(s). Re-run the same command to retry missing jobs.')


if __name__ == '__main__':
    main()
