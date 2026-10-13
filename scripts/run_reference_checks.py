"""Run reference-patch checks for every task and maintain a compact report."""
import csv
import json
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPLITS = ('train', 'dev', 'val')
REPORT = ROOT / 'reports' / 'reference_patch_checks.csv'
SUMMARY_RE = re.compile(
    r'(?:(\d+) failed,?\s*)?(?:(\d+) passed,?\s*)?(?:(\d+) skipped,?\s*)?'
    r'(?:(\d+) xfailed,?\s*)?(?:(\d+) xpassed,?\s*)?(?:(\d+) errors?)?'
    r'(?:\s+in\s+[\d.]+s)?$'
)
FIELDS = (
    'task_id', 'split', 'resolved', 'test_exit_code', 'passed', 'failed', 'skipped',
    'xfailed', 'xpassed', 'errors', 'partial_pass', 'error', 'result_path',
)


def tasks() -> list[tuple[str, str]]:
    rows = []
    for split in SPLITS:
        with (ROOT / 'data' / split / 'manifest.csv').open(newline='', encoding='utf-8') as file:
            rows.extend((row['task_id'], split) for row in csv.DictReader(file))
    return rows


def test_counts(output: str) -> dict[str, int]:
    counts = dict.fromkeys(('failed', 'passed', 'skipped', 'xfailed', 'xpassed', 'errors'), 0)
    for line in reversed(output.splitlines()):
        match = SUMMARY_RE.fullmatch(line.strip())
        if match and any(match.groups()):
            for key, value in zip(counts, match.groups()):
                counts[key] = int(value or 0)
            break
    return counts


def concise_error(result: dict) -> str:
    if result.get('error'):
        return str(result['error']).replace('\n', ' ')[:500]
    output = str(result.get('test_output', ''))
    for line in output.splitlines():
        if line.startswith(('E ', 'FAILED ', 'ERROR ')):
            return line[:500]
    return ''


def write_report(rows: dict[str, dict]) -> None:
    REPORT.parent.mkdir(exist_ok=True)
    with REPORT.open('w', newline='', encoding='utf-8') as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows[task_id] for task_id in sorted(rows))


def existing_rows() -> dict[str, dict]:
    if not REPORT.exists():
        return {}
    with REPORT.open(newline='', encoding='utf-8') as file:
        return {row['task_id']: row for row in csv.DictReader(file)}


def main() -> None:
    rows = existing_rows()
    all_tasks = tasks()
    for index, (task_id, split) in enumerate(all_tasks, start=1):
        if task_id in rows:
            continue
        print(f'[{index}/{len(all_tasks)}] {split}/{task_id}', flush=True)
        subprocess.run(
            [sys.executable, 'scripts/evaluate.py', '--task-id', task_id,
             '--split', split, '--reference-check'],
            cwd=ROOT,
            stdout=sys.stdout,
            stderr=sys.stderr,
            check=False,
        )
        result_path = ROOT / 'logs' / 'remote' / split / task_id / 'checks' / 'reference' / 'result.json'
        if not result_path.exists():
            rows[task_id] = dict.fromkeys(FIELDS, '')
            rows[task_id].update(task_id=task_id, split=split, resolved='false',
                                 error='Reference check did not produce result.json', result_path=str(result_path))
        else:
            result = json.loads(result_path.read_text(encoding='utf-8'))
            counts = test_counts(str(result.get('test_output', '')))
            rows[task_id] = {
                'task_id': task_id, 'split': split,
                'resolved': str(bool(result.get('resolved'))).lower(),
                'test_exit_code': result.get('test_exit_code', ''),
                **counts,
                'partial_pass': str(not result.get('resolved') and counts['passed'] > 0).lower(),
                'error': concise_error(result), 'result_path': str(result_path),
            }
        write_report(rows)


if __name__ == '__main__':
    main()
