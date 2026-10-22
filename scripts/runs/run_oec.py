"""Plan and run student-to-Haiku handoff attempts for the OEC dataset."""
import argparse
import csv
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from handoff_llm import draw_switch_points

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


def command_for(job: dict, args) -> list[str]:
    return [sys.executable, 'scripts/runs/task_pipeline.py',
            '--split', job['split'], '--task-id', job['task_id'],
            '--submission', str(args.submission), '--run-name', job['run_name'],
            '--provider', 'vllm', '--model', args.student_model, '--api-base', args.api_base,
            '--handoff-teacher-model', args.teacher_model,
            '--handoff-teacher-thinking', args.teacher_thinking,
            '--handoff-after', str(job['switch_after']),
            '--sandbox', args.sandbox, '--skip-langfuse']


def job_done(root: Path, submission_rel: Path, job: dict) -> bool:
    path = (root / 'logs/remote' / submission_rel / job['run_name'] / job['split']
            / job['task_id'] / 'results' / f"{job['task_id']}.json")
    try:
        result = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(result.get('resolved'), bool) and not result.get('error')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-prefix', required=True, help='Run name prefix, e.g. oec-v1; attempts become <prefix>-a1...')
    p.add_argument('--api-base', required=True, help='Student vLLM base, e.g. http://127.0.0.1:8002/v1')
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
    todo = [job for job in jobs if not job_done(ROOT, submission_rel, job)]
    print(f'{len(tasks)} verified tasks x {args.attempts} attempts', flush=True)
    print(f'{len(jobs)} planned, {len(jobs) - len(todo)} already done, {len(todo)} to run', flush=True)
    if args.dry_run:
        for job in todo:
            print(json.dumps(job))
        return
    log_dir = ROOT / 'logs/oec_runs' / args.run_prefix
    log_dir.mkdir(parents=True, exist_ok=True)

    def run(job):
        log = log_dir / f"{job['run_name']}-{job['task_id']}.log"
        with log.open('w') as output:
            code = subprocess.run(command_for(job, args), cwd=ROOT, stdout=output,
                                  stderr=subprocess.STDOUT).returncode
        print(f"{job['run_name']} {job['task_id']} k={job['switch_after']} exit={code}", flush=True)
        return code

    with ThreadPoolExecutor(args.workers) as pool:
        codes = list(pool.map(run, todo))
    failed = sum(code != 0 for code in codes)
    print(f'finished; {failed} process failure(s). Re-run the same command to retry missing jobs.')


if __name__ == '__main__':
    main()
