"""Check which tasks resolve on their own reference patch in the subprocess sandbox.

Run on a Kaggle kernel (Python 3.13 harness env, no Docker, no internet) before
spending student rollouts. For each task, `verify_task` runs on `task.patch`
with the task's wheel cache (manifest `wheels_dir`) or the shared wheels.

  preflight_reference.py --task-ids T1 T2 [--concurrency N] \
      [--wheels-dir-for-all DIR] --out preflight.json

Each task runs in its own subprocess (`--one`), so swegemma's fixed wheel cache
filename never collides across tasks. The parent only uses the stdlib; swegemma
is imported inside the child.
"""
import argparse
import csv
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPLITS = ('train', 'dev', 'val')
TASK_TIMEOUT_SECONDS = 600
REASON_LIMIT = 300
RESULT_KEYS = ('task_id', 'passed', 'reason', 'wheel_source', 'seconds')


def has_pytest_wheel(directory: Path | None) -> bool:
    if directory is None or not directory.is_dir():
        return False
    return any(path.name.lower().startswith('pytest-') and path.suffix == '.whl'
               for path in directory.iterdir())


def is_shared(task_dir: Path, shared_dir: Path | None) -> bool:
    """True when the task dir resolves to, or inside, the shared wheel dir (e.g. a symlink)."""
    if shared_dir is None:
        return False
    resolved = task_dir.resolve()
    return resolved == shared_dir.resolve() or resolved.is_relative_to(shared_dir.resolve())


def choose_wheel_dir(task_dir: Path | None, shared_dir: Path | None) -> tuple[Path | None, str]:
    """Prefer the task's own wheel cache, then the shared wheels; both need pytest.

    A task dir that is a symlink into the shared dir is reported as 'shared', since
    that is what the child actually installs from.
    """
    if has_pytest_wheel(task_dir):
        return task_dir, 'shared' if is_shared(task_dir, shared_dir) else 'task-cache'
    if has_pytest_wheel(shared_dir):
        return shared_dir, 'shared'
    return None, 'none'


def find_manifest_row(task_id: str, data_dir: Path) -> tuple[str | None, dict | None]:
    for split in SPLITS:
        manifest = data_dir / split / 'manifest.csv'
        if not manifest.is_file():
            continue
        with manifest.open(newline='', encoding='utf-8') as file:
            for row in csv.DictReader(file):
                if row['task_id'] == task_id:
                    return split, row
    return None, None


def plan_task(task_id: str, shared_dir: Path | None, root: Path = ROOT) -> dict:
    """Resolve asset paths and the wheel dir for one task, the way task_pipeline does."""
    split, row = find_manifest_row(task_id, root / 'data')
    task_dir = root / row['wheels_dir'] if row and row.get('wheels_dir') else None
    wheels_dir, source = choose_wheel_dir(task_dir, shared_dir)
    assets = {}
    for name, column in (('snapshot', 'snapshot_file'), ('graph', 'graph_file'),
                         ('embeddings', 'embedding_file')):
        if row and row.get(column):
            assets[name] = root / row[column]
    return {
        'task_id': task_id, 'split': split, 'wheels_dir': wheels_dir,
        'wheel_source': source, 'assets': assets,
    }


def failure(task_id: str, reason: str, wheel_source: str, seconds: float) -> dict:
    return {'task_id': task_id, 'passed': False, 'reason': reason[:REASON_LIMIT],
            'wheel_source': wheel_source, 'seconds': seconds}


def merge_results(results: list[dict]) -> dict:
    merged = {'passed': [], 'failed': {}, 'wheel_source': {}, 'seconds': {}}
    for result in results:
        task_id = result['task_id']
        merged['wheel_source'][task_id] = result['wheel_source']
        merged['seconds'][task_id] = float(result['seconds'])
        if result['passed']:
            merged['passed'].append(task_id)
        else:
            merged['failed'][task_id] = str(result['reason'])[:REASON_LIMIT]
    return merged


def parse_single_result(text: str) -> dict:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f'not JSON: {error}') from error
    if not isinstance(data, dict) or any(key not in data for key in RESULT_KEYS):
        raise ValueError(f'missing one of {RESULT_KEYS}')
    if not isinstance(data['passed'], bool):
        raise ValueError('passed must be a boolean')
    return {key: data[key] for key in RESULT_KEYS}


def run_child(argv: list[str], timeout: float) -> subprocess.CompletedProcess:
    """Run one task child in its own process group so a timeout kills its sandbox too."""
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, start_new_session=True)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()
        raise
    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)


def preflight_task(task_id: str, shared_dir: Path | None, root: Path = ROOT,
                   runner=run_child, timeout: float = TASK_TIMEOUT_SECONDS) -> dict:
    started = time.perf_counter()
    plan = plan_task(task_id, shared_dir, root)
    source = plan['wheel_source']
    if plan['wheels_dir'] is None:
        return failure(task_id, 'no wheel cache', 'none', 0.0)

    def elapsed() -> float:
        return round(time.perf_counter() - started, 3)

    with tempfile.TemporaryDirectory(prefix='preflight-result-') as result_dir:
        result_file = Path(result_dir) / 'result.json'
        argv = [sys.executable, str(Path(__file__).resolve()), '--one', task_id,
                '--root', str(root), '--wheels-dir', str(plan['wheels_dir']),
                '--wheel-source', source, '--result-file', str(result_file)]
        if plan['split']:
            argv += ['--split', plan['split']]
        for name, flag in (('snapshot', '--snapshot'), ('graph', '--graph'),
                           ('embeddings', '--embeddings')):
            if name in plan['assets']:
                argv += [flag, str(plan['assets'][name])]
        try:
            completed = runner(argv, timeout)
        except subprocess.TimeoutExpired:
            return failure(task_id, 'timeout', source, elapsed())
        if result_file.is_file():
            try:
                result = parse_single_result(result_file.read_text(encoding='utf-8'))
            except ValueError as error:
                return failure(task_id, f'unreadable child result: {error}', source, elapsed())
            result['seconds'] = elapsed()
            return result
        output = (completed.stderr or completed.stdout or '').strip()[-600:]
        return failure(task_id, f'exit {completed.returncode}: {output}', source, elapsed())


def verify_one(args: argparse.Namespace) -> dict:
    """Child body: verify the reference patch with swegemma's subprocess sandbox."""
    # Imported here so the parent and the unit tests never need swegemma.
    import asyncio

    from adk_submission import ModelRegistry
    from swegemma.config import EvalConfig
    from swegemma.evaluate import Evaluator
    from swegemma.harness import agent_runner, verification
    from swegemma.harness.verification import verify_task
    from swegemma.models import load_tasks
    from subprocess_eval_setup import install_subprocess_dependencies

    agent_runner.install_test_dependencies = install_subprocess_dependencies
    verification.install_test_dependencies = install_subprocess_dependencies
    root = args.root
    os.chdir(root)
    os.environ.setdefault('KAGGLE_SANDBOX_DIR', str(root / 'data/sandbox'))
    tasks_path = root / 'data' / args.split / 'tasks.jsonl' if args.split else root / 'data/tasks.jsonl'
    snapshot = args.snapshot or root / 'data/assets/snapshots' / f'{args.one}.tgz'
    graph_dir = args.graph.parent if args.graph else root / 'data/assets/graph'
    embeddings_dir = args.embeddings.parent if args.embeddings else root / 'data/assets/embeddings'

    async def verify(results_dir: Path):
        results_dir.mkdir(parents=True, exist_ok=True)
        config = EvalConfig(
            tasks_path=tasks_path, snapshots_dir=snapshot.parent, submission_dir=root / 'src',
            results_dir=results_dir, graph_dir=str(graph_dir),
            embeddings_dir=str(embeddings_dir), wheels_dir=args.wheels_dir,
            models=ModelRegistry(), task_ids=[args.one], sandbox='subprocess',
            image='swebench-sandbox:latest', max_tool_calls=50, max_time_minutes=30,
            timeout_seconds=300, concurrency=1, display_mode='auto',
        )
        evaluator = Evaluator(config)
        task = next((t for t in load_tasks(config.tasks_path) if t.instance_id == args.one), None)
        if task is None:
            raise ValueError(f'{args.one} not in {tasks_path}')
        return await verify_task(evaluator.sandbox, config, task, snapshot,
                                 agent_patch=task.patch, start_time=time.perf_counter())

    started = time.perf_counter()
    # swegemma caches unpacked wheels under a fixed temp filename; isolate each child.
    with tempfile.TemporaryDirectory(prefix='gemma-preflight-') as cache_dir:
        tempfile.tempdir = cache_dir
        try:
            result = asyncio.run(verify(Path(cache_dir) / 'results'))
            passed = bool(result.resolved)
            reason = '' if passed else (
                result.error_message or f'not resolved (test_exit_code={result.test_exit_code})')
        except Exception as error:  # record and move on; one bad task must not stop the run
            passed, reason = False, f'{type(error).__name__}: {error}'
        finally:
            tempfile.tempdir = None
    return {'task_id': args.one, 'passed': passed, 'reason': str(reason)[:REASON_LIMIT],
            'wheel_source': args.wheel_source, 'seconds': round(time.perf_counter() - started, 3)}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--task-ids', nargs='+', metavar='TASK_ID')
    p.add_argument('--concurrency', type=int, default=2, help='Parallel task subprocesses (default 2).')
    p.add_argument('--wheels-dir-for-all', type=Path,
                   help='Shared wheel dir used when a task cache has no pytest wheel.')
    p.add_argument('--out', type=Path, help='Merged preflight JSON output path.')
    p.add_argument('--root', type=Path, default=ROOT, help=argparse.SUPPRESS)
    # Child mode (one task); the parent passes these.
    p.add_argument('--one', metavar='TASK_ID', help=argparse.SUPPRESS)
    p.add_argument('--split', choices=SPLITS, help=argparse.SUPPRESS)
    p.add_argument('--wheels-dir', type=Path, help=argparse.SUPPRESS)
    p.add_argument('--wheel-source', choices=('task-cache', 'shared', 'none'), help=argparse.SUPPRESS)
    p.add_argument('--result-file', type=Path, help=argparse.SUPPRESS)
    p.add_argument('--snapshot', type=Path, help=argparse.SUPPRESS)
    p.add_argument('--graph', type=Path, help=argparse.SUPPRESS)
    p.add_argument('--embeddings', type=Path, help=argparse.SUPPRESS)
    args = p.parse_args(argv)
    if args.one is not None:
        if args.wheels_dir is None or args.result_file is None or args.wheel_source is None:
            p.error('--one needs --wheels-dir, --wheel-source and --result-file')
        return args
    if not args.task_ids:
        p.error('--task-ids is required')
    if args.out is None:
        p.error('--out is required')
    if args.concurrency < 1:
        p.error('--concurrency must be at least 1')
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.one is not None:
        result = verify_one(args)
        args.result_file.write_text(json.dumps(result), encoding='utf-8')
        return 0

    task_ids = list(dict.fromkeys(args.task_ids))
    shared = args.wheels_dir_for_all

    def safe(task_id: str) -> dict:
        try:
            return preflight_task(task_id, shared, args.root)
        except Exception as error:  # the parent must still record every task
            return failure(task_id, f'{type(error).__name__}: {error}', 'none', 0.0)

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        results = list(pool.map(safe, task_ids))
    merged = merge_results(results)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(merged, indent=2) + '\n', encoding='utf-8')
    print(f"preflight: passed {len(merged['passed'])}/{len(task_ids)}; wrote {args.out}", flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
