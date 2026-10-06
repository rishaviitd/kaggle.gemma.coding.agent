"""Run a local SWE task using a remote OpenAI-compatible model server."""
import argparse
import asyncio
import csv
import json
import os
import shutil
import tempfile
from pathlib import Path

from dotenv import load_dotenv
import litellm
import requests
import yaml
from google.adk.apps._configs import EventsCompactionConfig
from swegemma.config import EvalConfig
from swegemma.evaluate import Evaluator
from swegemma.harness.container_setup import _is_wheel_compatible_py313
from swegemma.models import load_tasks, setup_gemma_model_registry

from langfuse_bridge import push_to_langfuse
from vllm_trace_proxy import VllmTraceProxy

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
    p.add_argument('--resume', action='store_true',
                   help='Skip tasks with an existing result file in this submission log directory.')
    p.add_argument('--tasks', type=Path, help='Override the selected split task list.')
    p.add_argument('--snapshot', type=Path, help='Override the task snapshot path.')
    p.add_argument('--graph', type=Path, help='Override the task graph path.')
    p.add_argument('--embeddings', type=Path, help='Override the task embeddings path.')
    p.add_argument('--wheels', type=Path, help='Override the task wheel-cache path.')
    p.add_argument('--submission', type=Path,
                   help='Agent directory under src/, for example src/rishav/itr-1.')
    # p.add_argument('--api-base', default=os.getenv('LOCAL_INFERENCE_URL', 'https://legacy-repeal-vowed.ngrok-free.dev/v1/'))
    p.add_argument('--api-base', default=os.getenv('LOCAL_INFERENCE_URL', 'http://127.0.0.1:18001/v1'))
    p.add_argument('--model', default='gemma4')
    p.add_argument('--iteration', type=int, default=1,
                   help='Positive experiment number used in Langfuse task names.')
    p.add_argument('--max-tool-calls', type=int,
                   help='Override max_tool_calls from the submission eval config.')
    p.add_argument('--max-minutes', type=float,
                   help='Override max_time_minutes from the submission eval config.')
    p.add_argument('--timeout-seconds', type=int,
                   help='Override timeout_seconds from the submission eval config.')
    p.add_argument('--max-turns', type=int,
                   help='Override max_turns from the submission eval config.')
    p.add_argument('--max-output-tokens', type=int,
                   help='Override max_output_tokens from the submission sampling config.')
    p.add_argument('--skip-langfuse', action='store_true',
                   help='Do not upload the completed local trace even when Langfuse is configured.')
    p.add_argument('--langfuse-session-id',
                   help='Override the generated Langfuse session ID for this run.')
    args = p.parse_args()
    split_dir = ROOT / 'data' / args.split
    args.tasks = args.tasks or split_dir / 'tasks.jsonl'
    if args.iteration < 1:
        p.error('--iteration must be a positive integer')
    if args.submission is None:
        p.error('--submission is required; choose an iteration such as src/rishav/itr-1')
    args.submission = args.submission.resolve()
    src_root = (ROOT / 'src').resolve()
    try:
        submission_name = args.submission.relative_to(src_root)
    except ValueError:
        p.error('--submission must be a directory under src/')
    if not (args.submission / 'agent.yaml').is_file():
        p.error(f'Missing agent.yaml in --submission: {args.submission}')
    eval_config_path = args.submission / 'eval_config.yaml'
    eval_config = yaml.safe_load(eval_config_path.read_text()) if eval_config_path.is_file() else {}
    if eval_config is None:
        eval_config = {}
    if not isinstance(eval_config, dict):
        p.error(f'Invalid evaluation config: {eval_config_path}')
    evaluation = eval_config.get('evaluation', {})
    if not isinstance(evaluation, dict):
        p.error(f'evaluation must be a mapping in {eval_config_path}')
    args.max_tool_calls = (
        args.max_tool_calls if args.max_tool_calls is not None
        else evaluation.get('max_tool_calls', 25)
    )
    args.max_minutes = (
        args.max_minutes if args.max_minutes is not None
        else evaluation.get('max_time_minutes', 5)
    )
    args.timeout_seconds = (
        args.timeout_seconds if args.timeout_seconds is not None
        else evaluation.get('timeout_seconds', 300)
    )
    args.max_turns = (
        args.max_turns if args.max_turns is not None
        else evaluation.get('max_turns')
    )
    args.logs = ROOT / 'logs' / 'remote' / submission_name / args.split

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
    if args.resume:
        def completed_result(task_id: str) -> bool:
            path = args.logs / task_id / 'results' / f'{task_id}.json'
            try:
                result = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, json.JSONDecodeError):
                return False
            return isinstance(result.get('resolved'), bool) and not result.get('error')

        completed = [task_id for task_id in args.task_ids if completed_result(task_id)]
        args.task_ids = [task_id for task_id in args.task_ids if task_id not in completed]
        print(f'Resuming: skipping {len(completed)} completed task(s); '
              f'{len(args.task_ids)} remaining.', flush=True)
        if not args.task_ids:
            p.error('No unfinished tasks remain for this submission and split.')
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
    shutil.copytree(args.submission, agent_dir, ignore=shutil.ignore_patterns('adapters', 'tools', 'tests'))
    for path in agent_dir.rglob('*.yaml'):
        lines = path.read_text().splitlines(keepends=True)
        path.write_text(''.join(line for line in lines if not line.startswith('adapter:')))
    sampling_path = agent_dir / 'configs/sampling.yaml'
    sampling = yaml.safe_load(sampling_path.read_text()) or {}
    if args.max_output_tokens is not None:
        sampling['max_output_tokens'] = args.max_output_tokens
    else:
        sampling.setdefault('max_output_tokens', 4096)
    sampling_path.write_text(yaml.safe_dump(sampling))


def print_run_config(args, agent_dir):
    sampling_path = agent_dir / 'configs/sampling.yaml'
    sampling = yaml.safe_load(sampling_path.read_text()) or {}
    thinking = sampling.get('thinking_config') or {}
    default = '<framework default>'
    print(
        'Sampling config: '
        f"temperature={sampling.get('temperature', default)}, "
        f"top_p={sampling.get('top_p', default)}, "
        f"max_output_tokens={sampling.get('max_output_tokens', default)}, "
        f"thinking_budget={thinking.get('thinking_budget', default)}, "
        f"include_thoughts={thinking.get('include_thoughts', default)}",
        flush=True,
    )
    print(
        f'Effective task limits: max_tool_calls={args.max_tool_calls}, '
        f'max_time_minutes={args.max_minutes}, '
        f'timeout_seconds={args.timeout_seconds}, '
        f'max_turns={args.max_turns if args.max_turns is not None else default}',
        flush=True,
    )


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
    task_logs = args.logs / task.instance_id
    task_results = task_logs / 'results'
    proxy = VllmTraceProxy(args.api_base)
    proxy.start()
    config = EvalConfig(
        tasks_path=args.tasks, snapshots_dir=snapshots, submission_dir=agent_dir,
        results_dir=task_logs, graph_dir=str(graph.parent),
        embeddings_dir=str(embeddings.parent), wheels_dir=wheels,
        models=setup_gemma_model_registry(api_base=proxy.api_base, api_key=key,
                                         served_model=args.model, num_retries=2),
        task_ids=[task.instance_id], sandbox='docker', image='swebench-sandbox:latest',
        max_tool_calls=args.max_tool_calls, max_time_minutes=args.max_minutes,
        timeout_seconds=args.timeout_seconds, max_turns=args.max_turns,
        concurrency=1, display_mode='auto',
        events_compaction_config=EventsCompactionConfig(
            compaction_interval=15, overlap_size=2, token_threshold=14336,
            event_retention_size=5),
    )
    task_results.mkdir(parents=True, exist_ok=True)
    print(f'[{index}/{total}] Running {task.instance_id} with remote model {args.model}; '
          'workspaces/tests run locally.', flush=True)
    try:
        result = await Evaluator(config).evaluate_task(task=task, task_index=index, total_tasks=total)
        (task_results / f'{task.instance_id}.patch').write_text(result.agent_patch or '')
        (task_results / f'{task.instance_id}.json').write_text(
            result.model_dump_json(indent=2, exclude={'trace'}))
        summary = {'task_id': task.instance_id, 'resolved': result.resolved,
                   'test_exit_code': result.test_exit_code,
                   'patch_chars': len(result.agent_patch or ''), 'tool_calls': result.tool_calls,
                   'error': result.error_message}
        print(json.dumps(summary), flush=True)
        return summary
    finally:
        proxy.stop()
        final = {'result': result.model_dump(mode='json', exclude={'trace'}) if 'result' in locals() else None}
        proxy.write_trace(
            task_logs / 'model_trace.json',
            run={'task_id': task.instance_id, 'upstream_api_base': args.api_base},
            final=final,
        )


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
    incompatible = []
    for task_id in args.task_ids:
        wheels = list(args.asset_rows[task_id]['wheels'].glob('*.whl'))
        packages = {wheel.name.split('-')[0] for wheel in wheels}
        supported = {wheel.name.split('-')[0] for wheel in wheels
                     if _is_wheel_compatible_py313(wheel.name)}
        if packages - supported:
            incompatible.append(f"{task_id}: {', '.join(sorted(packages - supported))}")
    if incompatible:
        raise ValueError('Packages have no Python 3.13-compatible wheel: '
                         + '; '.join(incompatible)
                         + '. Repair caches with scripts/repair_task_wheels.py in Python 3.13 Linux Docker.')
    key = os.getenv('VLLM_API_KEY') or os.getenv('VLLMAPI_KEY')
    if not key:
        raise ValueError('Set VLLM_API_KEY in .env')
    sandbox_setup_dir = ROOT / 'data' / 'sandbox'
    if (sandbox_setup_dir / 'setup.py').is_file():
        os.environ.setdefault('KAGGLE_SANDBOX_DIR', str(sandbox_setup_dir))
    litellm.drop_params = True
    agent_dir = working / 'agent'
    prepare_agent(args, agent_dir)
    print_run_config(args, agent_dir)
    preflight_model(args, key)
    args.logs.mkdir(parents=True, exist_ok=True)
    for index, task_id in enumerate(args.task_ids, start=1):
        task_logs = args.logs / task_id
        if task_logs.exists():
            shutil.rmtree(task_logs)
        task_working = working / f'{index:03d}-{task_id}'
        task_working.mkdir()
        # The harness uses a fixed sp_base.tar name for unpacked dependencies.
        # Different tasks must never share that cached archive.
        cache_dir = task_working / 'cache'
        cache_dir.mkdir()
        previous_tempdir = tempfile.tempdir
        try:
            tempfile.tempdir = str(cache_dir)
            summary = await run_one(args, task_working, agent_dir, key,
                                    tasks[task_id], index, len(args.task_ids))
        except Exception as error:
            summary = {'task_id': task_id, 'resolved': False, 'error': str(error)}
            print(json.dumps(summary), flush=True)
            result_path = args.logs / task_id / 'results' / f'{task_id}.json'
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(json.dumps(summary, indent=2))
        finally:
            tempfile.tempdir = previous_tempdir


if __name__ == '__main__':
    load_dotenv(ROOT / '.env')
    args = arguments()
    # Avoid the harness's stale global unpacked-wheel cache.
    with tempfile.TemporaryDirectory(prefix='gemma-pipeline-') as tmp:
        previous = tempfile.tempdir
        tempfile.tempdir = tmp
        try:
            asyncio.run(run(args, Path(tmp)))
        finally:
            tempfile.tempdir = previous
    if not args.skip_langfuse:
        upload_ids = [task_id for task_id in args.task_ids
                      if (args.logs / task_id / 'traces' / f'trace_{task_id}.json').is_file()]
        if not upload_ids:
            print('Langfuse upload skipped: no ATIF trace was generated.', flush=True)
        for task_id in upload_ids:
            task_logs = args.logs / task_id
            task_heading = f'{task_id}-itr-{args.iteration}'
            push_to_langfuse(
                artifact_dir=task_logs,
                results_dir=task_logs / 'results',
                task_ids=[task_id],
                session_id=args.langfuse_session_id or task_heading,
                trace_name=task_heading,
                source_platform='vllm',
            )
