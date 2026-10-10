"""Prepare offline reference patches and task-specific baseline files.

The reference data is for post-run analysis only. Never expose this directory
to an agent that is solving the tasks.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import quote


def old_paths(patch):
    return {line[6:] for line in patch.splitlines()
            if line.startswith('--- a/') and line[6:] != 'dev/null'}


def safe_path(value):
    path = Path(value)
    if path.is_absolute() or '..' in path.parts or not path.parts:
        raise ValueError(f'Unsafe patch path: {value}')
    return path


def fetch(item):
    repo, commit, path, dest = item
    if dest.is_file():
        return dest
    url = f'https://raw.githubusercontent.com/{repo}/{commit}/{quote(path, safe="/")}'
    result = subprocess.run(
        ['curl', '--fail', '--location', '--silent', '--show-error',
         '--retry', '3', '--max-time', '45', url], capture_output=True, timeout=200,
    )
    if result.returncode:
        if b'404' in result.stderr:
            return None
        raise RuntimeError(f'Could not fetch {repo}@{commit}:{path}: '
                           f'{result.stderr.decode(errors="replace").strip()}')
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(result.stdout)
    return dest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tasks-jsonl', type=Path, required=True)
    parser.add_argument('--trace-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()

    traces = sorted(args.trace_dir.glob('*/model_trace.json'))
    if not traces:
        raise ValueError('No model_trace.json files found')
    tasks = {row['instance_id']: row for line in args.tasks_jsonl.open()
             if (row := json.loads(line))}
    work = []
    roots = {}
    for trace in traces:
        task_id = trace.parent.name
        task = tasks.get(task_id)
        if not task or not task.get('patch'):
            raise ValueError(f'Missing reference patch for {task_id}')
        repo, commit = task['repo'], task['base_commit']
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
            raise ValueError(f'Invalid repository name: {repo}')
        if not re.fullmatch(r'[0-9a-f]{40}', commit):
            raise ValueError(f'Invalid baseline commit: {commit}')
        patch_dir = args.output_dir / 'patches'
        patch_dir.mkdir(parents=True, exist_ok=True)
        (patch_dir / f'{task_id}.patch').write_text(task['patch'])
        baseline = (args.output_dir / 'baselines' / task_id).resolve()
        roots[task_id] = str(baseline)
        model_trace = json.loads(trace.read_text())
        agent_patch = model_trace.get('final', {}).get('result', {}).get('agent_patch') or ''
        for file in old_paths(task['patch']) | old_paths(agent_patch):
            path = safe_path(file)
            work.append((repo, commit, path.as_posix(), baseline / path))

    missing_baseline = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = [pool.submit(fetch, item) for item in work]
        for job in as_completed(jobs):
            missing_baseline += job.result() is None
    mapping = args.output_dir / 'repo_roots.json'
    mapping.write_text(json.dumps(roots, indent=2) + '\n')
    print(f'Prepared {len(roots)} reference patches and {len(work)-missing_baseline} baseline files')
    if missing_baseline:
        print(f'Paths absent at baseline commit: {missing_baseline}')
    print(f'Gold directory: {args.output_dir / "patches"}')
    print(f'Baseline mapping: {mapping}')


if __name__ == '__main__':
    main()
