"""Run one task, or verify its reference fix without calling a model."""
import argparse
import asyncio
import os
import time
import tempfile
from pathlib import Path

from swegemma.config import EvalConfig
from swegemma.evaluate import Evaluator
from swegemma.harness.verification import verify_task
from swegemma.models import load_tasks, setup_gemma_model_registry

ROOT = Path(__file__).resolve().parents[1]


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--task-id', default='fastapi_9753')
    parser.add_argument('--reference-check', action='store_true')
    args = parser.parse_args()
    os.chdir(ROOT)
    config = EvalConfig(
        tasks_path=ROOT / 'data/tasks.jsonl',
        snapshots_dir=ROOT / 'data/snapshots',
        submission_dir=ROOT / 'src',
        results_dir=ROOT / 'results' / ('reference' if args.reference_check else 'baseline'),
        graph_dir=str(ROOT / 'data/graph'),
        embeddings_dir=str(ROOT / 'data/embeddings'),
        wheels_dir=ROOT / 'data/wheels-fastapi-9753',
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
    if args.reference_check:
        task = next(t for t in load_tasks(config.tasks_path) if t.instance_id == args.task_id)
        print(f'Verifying reference fix for {args.task_id}; no model requests.', flush=True)
        result = await verify_task(
            evaluator.sandbox, config, task,
            config.snapshots_dir / f'{args.task_id}.tgz',
            agent_patch=task.patch,
            start_time=time.perf_counter(),
        )
        output = config.results_dir / f'{args.task_id}.json'
        output.write_text(result.model_dump_json(indent=2))
        print(f'Reference resolved: {result.resolved}. Details: {output}', flush=True)
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
