"""Run parallel isolated attempts per task, optionally including an existing run."""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]


def read_result(path):
    try:
        result = json.loads(path.read_text())
    except (OSError, ValueError):
        return 'missing'
    if not isinstance(result, dict):
        return 'error'
    if result.get('error') or result.get('error_message'):
        return 'error'
    if result.get('resolved') is True:
        return 'passed'
    return 'failed' if result.get('resolved') is False else 'error'


def run_attempts(jobs):
    """Start the whole batch before waiting; stop children on interruption."""
    children = []
    try:
        for command, log in jobs:
            with log.open('w') as output:
                children.append(subprocess.Popen(command, cwd=ROOT, stdout=output,
                                                 stderr=subprocess.STDOUT))
        return [child.wait() for child in children]
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--submission', type=Path, required=True)
    parser.add_argument('--provider', choices=('anthropic', 'openai'), required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--run-name', required=True)
    parser.add_argument('--parallel', type=int, default=3,
                        help='Number of new parallel attempts per task (default: 3).')
    parser.add_argument('--baseline-run',
                        help='Existing run under this submission to count as one prior attempt.')
    parser.add_argument('--task-ids', nargs='+', required=True)
    parser.add_argument('--split', choices=('train', 'dev', 'val'), default='val')
    parser.add_argument('--api-base')
    parser.add_argument('--reasoning-effort', choices=('minimal', 'low', 'medium', 'high'))
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    if args.parallel < 1:
        parser.error('--parallel must be positive')
    load_dotenv(ROOT / '.env')
    if args.provider == 'openai' and os.getenv('OPENAI_API_KEY_NEW'):
        os.environ['OPENAI_API_KEY'] = os.environ['OPENAI_API_KEY_NEW']
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', args.run_name):
        parser.error('Use only letters, numbers, dots, underscores and hyphens in --run-name')
    if args.baseline_run and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', args.baseline_run):
        parser.error('--baseline-run must be a run name, not a path')
    submission = args.submission.resolve()
    try:
        relative = submission.relative_to(ROOT / 'src')
    except ValueError:
        parser.error('--submission must be under src/')
    if not (submission / 'agent.yaml').is_file():
        parser.error('Submission is missing agent.yaml')
    tasks = list(dict.fromkeys(task for item in args.task_ids for task in item.split(',') if task))
    available = {json.loads(line)['instance_id'] for line in
                 (ROOT / 'data' / args.split / 'tasks.jsonl').read_text().splitlines() if line.strip()}
    if not tasks or any(task not in available for task in tasks):
        parser.error('Every task ID must belong to the selected split')
    base = ROOT / 'logs' / 'remote' / relative
    roots = [base / f'{args.run_name}-attempt-{i}' / args.split for i in range(1, args.parallel + 1)]
    baseline = base / args.baseline_run / args.split if args.baseline_run else None
    if baseline is not None:
        if baseline in roots or args.baseline_run == args.run_name:
            parser.error('Baseline must be separate from the new run')
        unfinished = [task for task in tasks if read_result(
            baseline / task / 'results' / f'{task}.json') not in ('passed', 'failed')]
        if unfinished:
            parser.error('Baseline has missing/error results; finish it first: ' + ', '.join(unfinished))
    summary_root = base / args.run_name
    if not args.resume and (summary_root.exists() or any(root.parent.exists() for root in roots)):
        parser.error('Run already exists; use a new --run-name or --resume')
    summary_root.mkdir(parents=True, exist_ok=True)
    total_attempts = args.parallel + int(baseline is not None)
    report = {'model': args.model, 'attempts_per_task': total_attempts,
              'parallel': args.parallel, 'baseline_run': args.baseline_run, 'tasks': {}}
    existing_summary = summary_root / 'summary.json'
    if args.resume and existing_summary.is_file():
        previous = json.loads(existing_summary.read_text())
        if any(previous.get(key) != report[key] for key in
               ('model', 'attempts_per_task', 'parallel', 'baseline_run')):
            parser.error('Resume settings differ from the saved run; use its original settings or a new run name')
    for index, task in enumerate(tasks, 1):
        jobs = []
        for attempt, root in enumerate(roots, 1):
            result = root / task / 'results' / f'{task}.json'
            if args.resume and read_result(result) in ('passed', 'failed'):
                continue
            command = [sys.executable, str(ROOT / 'scripts/runs/task_pipeline.py'),
                       '--split', args.split, '--submission', str(submission),
                       '--provider', args.provider, '--model', args.model,
                       '--run-name', f'{args.run_name}-attempt-{attempt}',
                       '--task-id', task, '--skip-langfuse']
            for name in ('api_base', 'reasoning_effort'):
                if getattr(args, name):
                    command += ['--' + name.replace('_', '-'), getattr(args, name)]
            jobs.append((command, summary_root / f'{task}-attempt-{attempt}.log'))
        print(f'[{index}/{len(tasks)}] {task}: starting {len(jobs)} parallel attempts', flush=True)
        run_attempts(jobs)
        statuses = [read_result(root / task / 'results' / f'{task}.json') for root in roots]
        if baseline is not None:
            statuses.insert(0, read_result(baseline / task / 'results' / f'{task}.json'))
        report['tasks'][task] = {'attempts': statuses, 'solved': 'passed' in statuses}
        report['solved'] = sum(item['solved'] for item in report['tasks'].values())
        report['total_tasks'] = len(tasks)
        report['recorded_tasks'] = len(report['tasks'])
        report['complete'] = len(report['tasks']) == len(tasks) and all(
            status in ('passed', 'failed') for item in report['tasks'].values() for status in item['attempts'])
        report[f'pass_at_{total_attempts}'] = report['solved'] / len(tasks)
        temporary = summary_root / 'summary.tmp'
        temporary.write_text(json.dumps(report, indent=2) + '\n')
        temporary.replace(summary_root / 'summary.json')
        print(f"{task}: {statuses}; solved {report['solved']}/{len(tasks)}", flush=True)
    print(f'Summary: {summary_root / "summary.json"}', flush=True)


if __name__ == '__main__':
    main()
