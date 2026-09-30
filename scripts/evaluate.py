"""Run one task, or verify its reference fix without calling a model."""
import argparse
import asyncio
import csv
import os
import time
import tempfile
from pathlib import Path

from swegemma.config import EvalConfig
from swegemma.evaluate import Evaluator
from swegemma.harness.verification import verify_task
from swegemma.models import load_tasks, setup_gemma_model_registry

ROOT = Path(__file__).resolve().parents[1]


def task_split(task_id: str, selected: str | None, parser: argparse.ArgumentParser) -> str:
    matches = []
    for split in ('train', 'dev', 'val'):
        with (ROOT / 'data' / split / 'manifest.csv').open(newline='') as file:
            if any(row['task_id'] == task_id for row in csv.DictReader(file)):
                matches.append(split)
    if selected:
        if selected not in matches:
            parser.error(f'{task_id} is not in the {selected} split')
        return selected
    if len(matches) != 1:
        parser.error(f'{task_id} belongs to {matches or "no split"}; pass --split when needed')
    return matches[0]


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--task-id', default='fastapi_11194')
    parser.add_argument('--split', choices=('train', 'dev', 'val'))
    verification = parser.add_mutually_exclusive_group()
    verification.add_argument('--reference-check', action='store_true')
    verification.add_argument('--patch-file', type=Path, help='Verify a saved agent patch without model inference')
    args = parser.parse_args()
    os.chdir(ROOT)
    mode = 'reference' if args.reference_check else ('reverification' if args.patch_file else 'baseline')
    split = task_split(args.task_id, args.split, parser)
    check_dir = ROOT / 'logs' / 'remote' / split / args.task_id / 'checks' / mode
    os.environ.setdefault('KAGGLE_SANDBOX_DIR', str(ROOT / 'data/sandbox'))
    config = EvalConfig(
        tasks_path=ROOT / 'data/tasks.jsonl',
        snapshots_dir=ROOT / 'data/assets/snapshots',
        submission_dir=ROOT / 'src',
        results_dir=check_dir,
        graph_dir=str(ROOT / 'data/assets/graph'),
        embeddings_dir=str(ROOT / 'data/assets/embeddings'),
        wheels_dir=ROOT / 'data/assets/task_wheels' / args.task_id,
        models=setup_gemma_model_registry(),
        task_ids=[args.task_id],
        sandbox='docker',
        image='swebench-sandbox:latest',
        max_tool_calls=50,
        max_time_minutes=30,
        timeout_seconds=300,
        concurrency=1,
        display_mode='auto',
    )
    config.results_dir.mkdir(parents=True, exist_ok=True)
    evaluator = Evaluator(config)
    if args.reference_check or args.patch_file:
        task = next(t for t in load_tasks(config.tasks_path) if t.instance_id == args.task_id)
        patch = args.patch_file.read_text() if args.patch_file else task.patch
        print(f'Verifying {mode} patch for {args.task_id}; no model requests.', flush=True)
        result = await verify_task(
            evaluator.sandbox, config, task,
            config.snapshots_dir / f'{args.task_id}.tgz',
            agent_patch=patch,
            start_time=time.perf_counter(),
        )
        output = check_dir / 'result.json'
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(result.model_dump_json(indent=2))
        print(f'Patch resolved: {result.resolved}. Details: {output}', flush=True)
        if not result.resolved:
            raise SystemExit(1)
    else:
        print(f'Evaluating {args.task_id}: 50 tool calls, 30 minutes.', flush=True)
        result = await evaluator.run()
        print(f'Resolved {result.resolved}/{result.total}', flush=True)


if __name__ == '__main__':
    # swegemma 0.2.7 caches unpacked wheels under a fixed temporary filename.
    # Isolate each run so previous dependency selections cannot contaminate it.
    with tempfile.TemporaryDirectory(prefix='gemma-eval-') as cache_dir:
        tempfile.tempdir = cache_dir
        try:
            asyncio.run(main())
        finally:
            tempfile.tempdir = None
