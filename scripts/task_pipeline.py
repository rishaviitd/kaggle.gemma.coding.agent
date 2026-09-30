"""Run a local SWE task using a remote OpenAI-compatible model server."""
import argparse
import asyncio
import csv
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
import litellm
import requests
import yaml
from google.adk.apps._configs import EventsCompactionConfig
from swegemma.config import EvalConfig
from swegemma.evaluate import Evaluator
from swegemma.models import load_tasks, setup_gemma_model_registry

from langfuse_bridge import push_to_langfuse

ROOT = Path(__file__).resolve().parents[1]


def arguments():
    p = argparse.ArgumentParser(description=__doc__)
    split_group = p.add_mutually_exclusive_group()
    split_group.add_argument('--split', choices=('train', 'dev', 'val'), default='val',
                             help='Dataset split to run (default: val).')
    split_group.add_argument('--train', dest='split', action='store_const', const='train',
                             help='Select the train split.')
    split_group.add_argument('--dev', dest='split', action='store_const', const='dev',
                             help='Select the dev split.')
    split_group.add_argument('--val', dest='split', action='store_const', const='val',
                             help='Select the val split.')
    task_group = p.add_mutually_exclusive_group()
    task_group.add_argument('--task-id', help='Run one task from the selected split.')
    task_group.add_argument('--task-ids', nargs='+', metavar='TASK_ID',
                            help='Run these task IDs (space- or comma-separated).')
    task_group.add_argument('--all', action='store_true',
                            help='Run every task in the selected split.')
    p.add_argument('--tasks', type=Path, help='Override the selected split task list.')
    p.add_argument('--snapshot', type=Path, help='Override the task snapshot path.')
    p.add_argument('--graph', type=Path, help='Override the task graph path.')
    p.add_argument('--embeddings', type=Path, help='Override the task embeddings path.')
    p.add_argument('--wheels', type=Path, help='Override the task wheel-cache path.')
    p.add_argument('--submission', type=Path, default=ROOT / 'src')
    p.add_argument('--results', type=Path, help='Override patch/result output directory.')
    p.add_argument('--api-base', default=os.getenv('LOCAL_INFERENCE_URL', 'https://legacy-repeal-vowed.ngrok-free.dev/v1/'))
    p.add_argument('--model', default='gemma4')
    p.add_argument('--max-tool-calls', type=int, default=50)
    p.add_argument('--max-minutes', type=float, default=10)
    p.add_argument('--max-output-tokens', type=int, default=4096)
    p.add_argument('--logs', type=Path, help='Override evaluation log/trace directory.')
    p.add_argument('--skip-langfuse', action='store_true',
                   help='Do not upload the completed local trace even when Langfuse is configured.')
    p.add_argument('--langfuse-session-id',
                   help='Override the generated Langfuse session ID for this run.')
    args = p.parse_args()
    split_dir = ROOT / 'data' / args.split
    args.tasks = args.tasks or split_dir / 'tasks.jsonl'
    args.results = args.results or ROOT / 'results' / 'remote' / args.split
    args.logs = args.logs or ROOT / 'logs' / 'remote' / args.split

    manifest = split_dir / 'manifest.csv'
    if manifest.is_file():
        with manifest.open(newline='', encoding='utf-8') as file:
            manifest_rows = list(csv.DictReader(file))
    else:
        p.error(f'Missing split manifest: {manifest}')

    available = {row['task_id']: row for row in manifest_rows}
    if args.all:
        args.task_ids = list(available)
    elif args.task_ids:
        args.task_ids = list(dict.fromkeys(
            task_id for item in args.task_ids for task_id in item.split(',') if task_id
        ))
    elif args.task_id:
        args.task_ids = [args.task_id]
    elif args.split == 'val' and 'fastapi_11194' in available:
        # Preserve the original one-task default for the existing reference task.
        args.task_ids = ['fastapi_11194']
    else:
        p.error('Choose --task-id, --task-ids, or --all')

    unknown = [task_id for task_id in args.task_ids if task_id not in available]
    if unknown:
        p.error(f"Task(s) not in {args.split}: {', '.join(unknown)}")
    if len(args.task_ids) > 1 and any(
        getattr(args, name) is not None for name in ('snapshot', 'graph', 'embeddings', 'wheels')
    ):
        p.error('Asset path overrides (--snapshot/--graph/--embeddings/--wheels) require one task')
    args.asset_rows = {
        task_id: {
            name: ROOT / available[task_id][column]
            for name, column in (
                ('snapshot', 'snapshot_file'), ('graph', 'graph_file'),
                ('embeddings', 'embedding_file'), ('wheels', 'wheels_dir'),
            )
        }
        for task_id in args.task_ids
    }
    if len(args.task_ids) == 1:
        task_id = args.task_ids[0]
        for name in ('snapshot', 'graph', 'embeddings', 'wheels'):
            if getattr(args, name) is None:
                setattr(args, name, args.asset_rows[task_id][name])
            else:
                args.asset_rows[task_id][name] = getattr(args, name)

    return args


def preflight_model(args, key):
    response = requests.post(
        args.api_base.rstrip('/') + '/chat/completions',
        headers={'Authorization': f'Bearer {key}', 'ngrok-skip-browser-warning': '1'},
        json={'model': args.model, 'messages': [{'role': 'user', 'content': 'Reply OK.'}],
              'tools': [{'type': 'function', 'function': {
                  'name': 'get_status', 'description': 'Get workspace status',
                  'parameters': {'type': 'object', 'properties': {}}}}],
              'tool_choice': 'auto', 'max_tokens': 32,
              'chat_template_kwargs': {'enable_thinking': False}},
        timeout=120,
    )
    if not response.ok:
        raise RuntimeError(f'Model tool-calling preflight failed (HTTP {response.status_code}): '
                           f'{response.text[:600]}')


def prepare_agent(args, agent_dir):
    shutil.copytree(args.submission, agent_dir, ignore=shutil.ignore_patterns('adapters', 'tools'))
    for path in agent_dir.rglob('*.yaml'):
        lines = path.read_text().splitlines(keepends=True)
        path.write_text(''.join(line for line in lines if not line.startswith('adapter:')))
    sampling_path = agent_dir / 'configs/sampling.yaml'
    sampling = yaml.safe_load(sampling_path.read_text())
    sampling['max_output_tokens'] = args.max_output_tokens
    sampling_path.write_text(yaml.safe_dump(sampling))


async def run_one(args, working, agent_dir, key, task, index, total):
    paths = args.asset_rows[task.instance_id]
    snapshot, graph = paths['snapshot'], paths['graph']
    embeddings, wheels = paths['embeddings'], paths['wheels']
    expected = f"{task.repo.rsplit('/', 1)[-1]}_{task.base_commit}"
    for path, suffix in [(graph, '.json'), (embeddings, '.npz')]:
        if path.name != expected + suffix:
            raise ValueError(f'{path.name} does not match task repo/base_commit')
    for path in (args.tasks, snapshot, graph, embeddings):
        if not path.is_file():
            raise FileNotFoundError(path)
    if not wheels.is_dir():
        raise FileNotFoundError(wheels)
    if not any(wheels.glob('*.whl')):
        raise ValueError(f'No wheels in {wheels}; build task caches with scripts/build_split_wheels.sh')

    snapshots = working / 'snapshots'
    snapshots.mkdir()
    (snapshots / f'{task.instance_id}.tgz').symlink_to(snapshot.resolve())
    config = EvalConfig(
        tasks_path=args.tasks, snapshots_dir=snapshots, submission_dir=agent_dir,
        results_dir=args.logs, graph_dir=str(graph.parent),
        embeddings_dir=str(embeddings.parent), wheels_dir=wheels,
        models=setup_gemma_model_registry(api_base=args.api_base, api_key=key,
                                         served_model=args.model, num_retries=2),
        task_ids=[task.instance_id], sandbox='docker', image='swebench-sandbox:latest',
        max_tool_calls=args.max_tool_calls, max_time_minutes=args.max_minutes,
        timeout_seconds=300, concurrency=1, display_mode='auto',
        events_compaction_config=EventsCompactionConfig(
            compaction_interval=15, overlap_size=2, token_threshold=14336,
            event_retention_size=5),
    )
    args.results.mkdir(parents=True, exist_ok=True)
    args.logs.mkdir(parents=True, exist_ok=True)
    print(f'[{index}/{total}] Running {task.instance_id} with remote model {args.model}; '
          'workspaces/tests run locally.', flush=True)
    result = await Evaluator(config).evaluate_task(task=task, task_index=index, total_tasks=total)
    (args.results / f'{task.instance_id}.patch').write_text(result.agent_patch or '')
    (args.results / f'{task.instance_id}.json').write_text(
        result.model_dump_json(indent=2, exclude={'trace'}))
    summary = {'task_id': task.instance_id, 'resolved': result.resolved,
               'test_exit_code': result.test_exit_code,
               'patch_chars': len(result.agent_patch or ''), 'tool_calls': result.tool_calls,
               'error': result.error_message}
    print(json.dumps(summary), flush=True)
    return summary


async def run(args, working):
    tasks = {task.instance_id: task for task in load_tasks(args.tasks)}
    missing = [task_id for task_id in args.task_ids if task_id not in tasks]
    if missing:
        raise ValueError(f'Task(s) missing from {args.tasks}: {", ".join(missing)}')
    missing_wheels = [task_id for task_id in args.task_ids
                      if not any(args.asset_rows[task_id]['wheels'].glob('*.whl'))]
    if missing_wheels:
        raise ValueError('Task wheel caches are empty for: ' + ', '.join(missing_wheels)
                         + '. Build them with scripts/build_split_wheels.sh')
    key = os.getenv('VLLM_API_KEY') or os.getenv('VLLMAPI_KEY')
    if not key:
        raise ValueError('Set VLLM_API_KEY in .env')
    sandbox_setup_dir = ROOT / 'data' / 'sandbox'
    if (sandbox_setup_dir / 'setup.py').is_file():
        os.environ.setdefault('KAGGLE_SANDBOX_DIR', str(sandbox_setup_dir))
    preflight_model(args, key)
    litellm.drop_params = True
    agent_dir = working / 'agent'
    prepare_agent(args, agent_dir)
    args.results.mkdir(parents=True, exist_ok=True)
    args.logs.mkdir(parents=True, exist_ok=True)
    summaries = []
    for index, task_id in enumerate(args.task_ids, start=1):
        task_working = working / f'{index:03d}-{task_id}'
        task_working.mkdir()
        try:
            summary = await run_one(args, task_working, agent_dir, key,
                                    tasks[task_id], index, len(args.task_ids))
        except Exception as error:
            summary = {'task_id': task_id, 'resolved': False, 'error': str(error)}
            print(json.dumps(summary), flush=True)
        summaries.append(summary)
        (args.results / 'batch_summary.json').write_text(json.dumps(summaries, indent=2))


if __name__ == '__main__':
    load_dotenv(ROOT / '.env')
    args = arguments()
    run_started = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    # Avoid the harness's stale global unpacked-wheel cache.
    with tempfile.TemporaryDirectory(prefix='gemma-pipeline-') as tmp:
        previous = tempfile.tempdir
        tempfile.tempdir = tmp
        try:
            asyncio.run(run(args, Path(tmp)))
        finally:
            tempfile.tempdir = previous
    if not args.skip_langfuse:
        session_id = args.langfuse_session_id or f'vllm-{args.split}-{run_started}'
        trace_dirs = (args.logs / 'results' / 'traces', args.logs / 'traces')
        traced_ids = {
            path.stem.removeprefix('trace_')
            for trace_dir in trace_dirs if trace_dir.is_dir()
            for path in trace_dir.glob('trace_*.json')
        }
        upload_ids = [task_id for task_id in args.task_ids if task_id in traced_ids]
        if upload_ids:
            push_to_langfuse(
                artifact_dir=args.logs,
                task_ids=upload_ids,
                session_id=session_id,
                source_platform='vllm',
            )
        else:
            print('Langfuse upload skipped: no ATIF trace was generated.', flush=True)
