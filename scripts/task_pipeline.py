"""Run a local SWE task using a remote OpenAI-compatible model server."""
import argparse
import asyncio
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
    p.add_argument('--task-id', default='fastapi_11194')
    p.add_argument('--tasks', type=Path, default=ROOT / 'data/tasks.jsonl')
    p.add_argument('--snapshot', type=Path, default=ROOT / 'data/snapshots/fastapi_11194.tgz')
    p.add_argument('--graph', type=Path, default=ROOT / 'data/graph/fastapi_a7f2dbe976bf72703376f0cd04487bfc4a849f83.json')
    p.add_argument('--embeddings', type=Path, default=ROOT / 'data/embeddings/fastapi_a7f2dbe976bf72703376f0cd04487bfc4a849f83.npz')
    p.add_argument('--wheels', type=Path, default=ROOT / 'data/wheels-fastapi-11194')
    p.add_argument('--submission', type=Path, default=ROOT / 'src')
    p.add_argument('--results', type=Path, default=ROOT / 'results/remote-baseline')
    p.add_argument('--api-base', default=os.getenv('LOCAL_INFERENCE_URL', 'https://legacy-repeal-vowed.ngrok-free.dev/v1/'))
    p.add_argument('--model', default='gemma4')
    p.add_argument('--max-tool-calls', type=int, default=50)
    p.add_argument('--max-minutes', type=float, default=10)
    p.add_argument('--max-output-tokens', type=int, default=4096)
    p.add_argument('--skip-langfuse', action='store_true',
                   help='Do not upload the completed local trace even when Langfuse is configured.')
    p.add_argument('--langfuse-session-id',
                   help='Override the generated Langfuse session ID for this run.')
    return p.parse_args()


async def run(args, working):
    task = next((t for t in load_tasks(args.tasks) if t.instance_id == args.task_id), None)
    if task is None:
        raise ValueError(f'Task not found: {args.task_id}')
    expected = f"{task.repo.rsplit('/', 1)[-1]}_{task.base_commit}"
    for path, suffix in [(args.graph, '.json'), (args.embeddings, '.npz')]:
        if path.name != expected + suffix:
            raise ValueError(f'{path.name} does not match task repo/base_commit')
    for path in (args.tasks, args.snapshot, args.graph, args.embeddings):
        if not path.is_file():
            raise FileNotFoundError(path)
    if not args.wheels.is_dir():
        raise FileNotFoundError(args.wheels)
    key = os.getenv('VLLM_API_KEY') or os.getenv('VLLMAPI_KEY')
    if not key:
        raise ValueError('Set VLLM_API_KEY in .env')
    # Check the same automatic tool-calling capability the agent requires.
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
    # Compile a temporary base-model submission; retain original submission adapters.
    agent_dir = working / 'agent'
    shutil.copytree(args.submission, agent_dir, ignore=shutil.ignore_patterns('adapters', 'tools'))
    for path in agent_dir.rglob('*.yaml'):
        lines = path.read_text().splitlines(keepends=True)
        path.write_text(''.join(line for line in lines if not line.startswith('adapter:')))
    sampling_path = agent_dir / 'configs/sampling.yaml'
    sampling = yaml.safe_load(sampling_path.read_text())
    sampling['max_output_tokens'] = args.max_output_tokens
    sampling_path.write_text(yaml.safe_dump(sampling))
    snapshots = working / 'snapshots'
    snapshots.mkdir()
    (snapshots / f'{task.instance_id}.tgz').symlink_to(args.snapshot.resolve())
    litellm.drop_params = True
    config = EvalConfig(
        tasks_path=args.tasks, snapshots_dir=snapshots, submission_dir=agent_dir,
        results_dir=args.results, graph_dir=str(args.graph.parent),
        embeddings_dir=str(args.embeddings.parent), wheels_dir=args.wheels,
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
    print(f'Running {task.instance_id} with remote model {args.model}; workspaces/tests run locally.', flush=True)
    result = await Evaluator(config).evaluate_task(task=task, task_index=1, total_tasks=1)
    (args.results / f'{task.instance_id}.patch').write_text(result.agent_patch or '')
    (args.results / f'{task.instance_id}.json').write_text(
        result.model_dump_json(indent=2, exclude={'trace'}))
    print(json.dumps({'resolved': result.resolved, 'test_exit_code': result.test_exit_code,
                      'patch_chars': len(result.agent_patch or ''), 'tool_calls': result.tool_calls,
                      'error': result.error_message}), flush=True)


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
        session_id = args.langfuse_session_id or f'vllm-{args.task_id}-{run_started}'
        push_to_langfuse(
            artifact_dir=args.results,
            task_ids=[args.task_id],
            session_id=session_id,
            source_platform='vllm',
        )
