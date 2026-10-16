"""Run the requested Luna-then-Sol comparison with temporary provider settings."""
import asyncio
import csv
import gzip
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from dotenv import dotenv_values, load_dotenv
import yaml

ROOT = next(p for p in Path(__file__).resolve().parents if (p / 'scripts/task_pipeline.py').is_file())
OUTPUT = Path(__file__).resolve().parent
RUN_ID = OUTPUT.name
TASKS = '''fastapi_12942 fastapi_14372 fastapi_14794 fastapi_5624 fastapi_14077
fastapi_14266 fastapi_14246 fastapi_14482 fastapi_14419 fastapi_14306 rich_3043
rich_3905 rich_4076 rich_3506 rich_3935 rich_3052 rich_3180 requests_7205
requests_7427 httpx_3672'''.split()
MODELS = ['gpt-6-luna', 'gpt-6.1-sol']


def results(model):
    rows = []
    for task in TASKS:
        path = ROOT / 'logs/remote/rishav/itr-1' / f'{RUN_ID}-{model}' / 'val' / task / 'results' / f'{task}.json'
        try:
            result = json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            result = {}
        rows.append({'model': model, 'task_id': task, 'resolved': result.get('resolved'),
                     'error': result.get('error_message') or result.get('error'),
                     'tool_calls': result.get('tool_calls'), 'duration_seconds': result.get('duration_seconds'),
                     'result_path': str(path)})
    return rows


def save_summary():
    rows = [row for model in MODELS for row in results(model)]
    totals = {}
    for model in MODELS:
        selected = [r for r in rows if r['model'] == model]
        totals[model] = {
            'solved': sum(r['resolved'] is True for r in selected),
            'failed': sum(r['resolved'] is False and not r['error'] for r in selected),
            'errors': sum(bool(r['error']) for r in selected),
            'missing': sum(r['resolved'] is None for r in selected),
        }
    summary = {'settings': {'tasks': TASKS, 'models_in_order': MODELS, 'reasoning_effort': 'low',
                            'reasoning_summary': 'auto', 'max_tool_calls': 40, 'max_minutes': 5.5,
                            'workers_per_model': 3, 'submission': 'src/rishav/itr-1'},
               'totals': totals, 'tasks': rows}
    (OUTPUT / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    with (OUTPUT / 'results.csv').open('w', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return totals


def worker(model, tasks):
    sys.path.insert(0, str(ROOT / 'scripts'))
    import task_pipeline as pipeline
    import vllm_trace_proxy as trace

    key_path = ROOT.parent / 'kaggle.gemma.coding.agent-ide/.env'
    key = dotenv_values(key_path).get('OPENAI_API_KEY_NEW')
    if not key:
        raise RuntimeError('OPENAI_API_KEY_NEW is missing')
    os.environ['OPENAI_API_KEY'] = key
    load_dotenv(ROOT / '.env')
    original_prepare = pipeline.prepare_agent
    original_model = pipeline.LiteLlm
    original_decode = trace._decode_json
    original_write = pipeline.VllmTraceProxy.write_trace

    def prepare(args, directory):
        original_prepare(args, directory)
        path = directory / 'configs/sampling.yaml'
        config = yaml.safe_load(path.read_text())
        for name in ('thinking_config', 'temperature', 'top_p'):
            config.pop(name, None)
        path.write_text(yaml.safe_dump(config))

    def provider(**kwargs):
        return original_model(**kwargs, reasoning_effort={'effort': 'low', 'summary': 'auto'})

    def write(proxy, path, **kwargs):
        original_write(proxy, path, **kwargs)
        calls = []
        for exchange in proxy.exchanges:
            response = exchange.get('response') or {}
            if not isinstance(response, dict):
                response = {}
            calls.append({'http_status': exchange['response_status'], 'id': response.get('id'),
                          'model': response.get('model'), 'usage': response.get('usage'),
                          'reasoning': response.get('reasoning'),
                          'reasoning_items': [item for item in response.get('output', []) if item.get('type') == 'reasoning'],
                          'error': response.get('error')})
        path.with_name('provider_audit.json').write_text(json.dumps(calls, indent=2) + '\n')

    pipeline.prepare_agent = prepare
    pipeline.LiteLlm = provider
    pipeline.VllmTraceProxy.write_trace = write
    trace._decode_json = lambda body: original_decode(gzip.decompress(body) if body.startswith(b'\x1f\x8b') else body)
    sys.argv = ['task_pipeline.py', '--val', '--task-ids', *tasks, '--submission', 'src/rishav/itr-1',
                '--run-name', f'{RUN_ID}-{model}', '--provider', 'openai', '--model', f'openai/responses/{model}',
                '--api-base', 'https://api.openai.com/v1', '--max-tool-calls', '40', '--max-minutes', '5.5',
                '--skip-langfuse', '--resume']
    args = pipeline.arguments()
    with tempfile.TemporaryDirectory(prefix='openai20-') as directory:
        previous = tempfile.tempdir
        tempfile.tempdir = directory
        try:
            asyncio.run(pipeline.run(args, Path(directory)))
        finally:
            tempfile.tempdir = previous


if __name__ == '__main__':
    os.chdir(ROOT)
    if len(sys.argv) > 1 and sys.argv[1] == '--worker':
        worker(sys.argv[2], sys.argv[3:])
    else:
        for model in MODELS:
            print(f'START {model}: 20 tasks', flush=True)
            processes = []
            for index in range(3):
                file = (OUTPUT / f'{model}-worker-{index + 1}.log').open('a')
                process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--worker', model, *TASKS[index::3]],
                                           stdout=file, stderr=subprocess.STDOUT)
                file.close()
                processes.append(process)
            previous = None
            while True:
                totals = save_summary()
                if totals[model] != previous:
                    print(model, json.dumps(totals[model]), flush=True)
                    previous = totals[model]
                if all(process.poll() is not None for process in processes):
                    break
                time.sleep(10)
            totals = save_summary()
            print(f'FINISHED {model}: {json.dumps(totals[model])}', flush=True)
            if any(process.returncode != 0 for process in processes):
                raise SystemExit(f'{model}: a worker failed; inspect its log before continuing.')
        print('FINAL', json.dumps(save_summary()), flush=True)
