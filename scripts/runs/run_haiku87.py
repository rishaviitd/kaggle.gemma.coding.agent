"""Run Haiku on the reference-passing task set."""
import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parallel', type=int, default=3,
                        help='Independent attempts per task (default: 3).')
    parser.add_argument('--run-name', default='haiku-5p5-inspiration-one-remaining87-20261018',
                        help='Unique batch name; split names are appended automatically.')
    parser.add_argument('--include-previous-20', action='store_true',
                        help='Include the 20 tasks from the earlier Haiku batch.')
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    if args.parallel < 1:
        parser.error('--parallel must be positive')

    previous_path = (ROOT / 'logs/remote/rishav/inspiration-one'
                     / 'haiku-5p5-inspiration-one-pass3-20261017/summary.json')
    previous = json.loads(previous_path.read_text())
    excluded = set(previous['tasks'])
    with (ROOT / 'docs/reports/reference_patch_checks.csv').open(newline='') as file:
        passed = {row['task_id']: row['split'] for row in csv.DictReader(file)
                  if row['resolved'].lower() == 'true' and row['test_exit_code'] == '0'}
    if (not previous.get('complete') or previous.get('attempts_per_task') != 3
            or len(excluded) != 20 or len(passed) != 107 or not excluded <= passed.keys()):
        parser.error('Expected 107 passing references and the completed 20-task Haiku pass@3 batch')
    selected = passed if args.include_previous_20 else {
        task: split for task, split in passed.items() if task not in excluded
    }
    description = 'including the previous 20 tasks' if args.include_previous_20 else 'excluding the previous 20 tasks'
    print(f'{len(selected)} tasks × {args.parallel} attempts = '
          f'{len(selected) * args.parallel} executions; {description}.', flush=True)

    for split in ('train', 'dev', 'val'):
        tasks = sorted(task for task, dataset in selected.items() if dataset == split)
        if not tasks:
            continue
        command = [sys.executable, str(ROOT / 'scripts/runs/run_pass3.py'),
                   '--split', split, '--submission', str(ROOT / 'src/rishav/inspiration-one'),
                   '--provider', 'anthropic', '--model', 'claude-haiku-5-5',
                   '--api-base', 'https://api.anthropic.com',
                   '--run-name', f'{args.run_name}-{split}',
                   '--parallel', str(args.parallel), '--task-ids', *tasks]
        if args.resume:
            command.append('--resume')
        print(f'Running {split}: {len(tasks)} tasks, {args.parallel} parallel attempts per task.', flush=True)
        subprocess.run(command, cwd=ROOT, check=True)


if __name__ == '__main__':
    main()
