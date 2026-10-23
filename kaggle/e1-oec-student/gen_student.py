"""E1 OEC run A on Kaggle: E4B base student only, stops at the handoff and writes snapshots.

Internet OFF, competition attached, L4. No Anthropic key, no teacher: Haiku continues locally from the
snapshot (scripts/runs/run_teacher_docker.sh). Layout:
- Python 3.12 (pinned competition image) installs the competition wheelhouse and serves the E4B base
  with adk_submission.VllmServer on 127.0.0.1:8002 as served name `gemma4`, no LoRA.
- Python 3.13 harness comes from the private dataset e1-oec-py313-env (h313.bin, a tar of /opt/h313 and the
  uv CPython 3.13), extracted to its original absolute paths. No network is needed.
- run_oec.py --stage student runs the harness under that 3.13 and writes <task>/snapshot/ per task.
"""
import glob
import importlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request
from pathlib import Path

os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] = 'True'
os.environ['TRANSFORMERS_NO_TF'] = '1'
os.environ['VLLM_WORKER_MULTIPROC_METHOD'] = 'spawn'
os.environ['VLLM_MEMORY_PROFILER_ESTIMATE_CUDAGRAPHS'] = '1'
os.environ['VLLM_ENGINE_READY_TIMEOUT_S'] = '1200'
os.environ['VLLM_NO_USAGE_STATS'] = '1'
os.environ['OTEL_SDK_DISABLED'] = 'true'
os.environ['PYTORCH_CUDA_ALLOC_CONF'] = 'expandable_segments:True'

WORKING = Path('/kaggle/working')
REPO = Path('/tmp/repo')
H313 = Path('/opt/h313')
PY313 = H313 / 'bin' / 'python'
WHEELHOUSE_DIRS = (Path('/kaggle/input/datasets/metric/gemma-4-developer-agent-wheelhouse'),
                   Path('/kaggle/input/gemma-4-developer-agent-wheelhouse'))
MODEL_INSTANCE = 'gemma-4-e4b-it-qat-w4a16-ct'
PORT = 8002  # first server; server i listens on PORT + i and is pinned to GPU i
SERVED_NAME = 'gemma4'
TASK_IDS = []  # filled from the assets dataset (task_ids.json) by link_assets()
ATTEMPTS = 5
SWITCH_LOW, SWITCH_HIGH = 2, 10  # base E4B runs: median 8 tool calls, p90 11; higher k hits compaction or finishes first
TEMPERATURE = 1.0  # student sampling temperature; run B must pass the same --temperature (config hash)
RUN_PREFIX = 'oec-v1'
HARNESS_DEPS = ['google-adk==1.36.1', 'litellm', 'python-dotenv', 'pyyaml', 'requests',
                'cachetools', 'docker', 'google-genai', 'networkx', 'numpy', 'pydantic', 'rich',
                'pandas', 'tokenizers']
SECRET = {'value': None}
REPORT = {}


def redact(text):
    text = str(text)
    if SECRET['value']:
        text = text.replace(SECRET['value'], '[REDACTED]')
    return text


def log(key, value):
    REPORT[key] = value
    print(redact(f'GEN {key}: {json.dumps(value, default=str)}'), flush=True)


def stage(msg):
    print(f'== {msg}', flush=True)


def write_report():
    WORKING.mkdir(parents=True, exist_ok=True)
    (WORKING / 'gen_student_report.json').write_text(redact(json.dumps(REPORT, indent=2, default=str)))


def run(cmd, **kw):
    stage(' '.join(map(str, cmd)))
    result = subprocess.run(list(map(str, cmd)), capture_output=True, text=True, **kw)
    out = (result.stdout or '')[-3000:] + (result.stderr or '')[-3000:]
    if out.strip():
        print(redact(out), flush=True)
    return result





def stage_repo():
    """Copy the code dataset to a writable root; run_oec.py's ROOT is the repo layout."""
    hits = sorted(glob.glob('/kaggle/input/**/scripts/runs/run_oec.py', recursive=True), key=len)
    if hits:
        src = Path(hits[0]).parents[2]
        log('code_root', str(src))
        shutil.copytree(src, REPO, symlinks=False, dirs_exist_ok=True)
    else:
        tars = sorted(glob.glob('/kaggle/input/**/e1-oec-code/**/*.tar', recursive=True))
        log('code_tars', tars)
        if not tars:
            raise SystemExit('code dataset not mounted (no run_oec.py and no .tar parts)')
        REPO.mkdir(parents=True, exist_ok=True)
        for part in tars:
            with tarfile.open(part) as tar:
                tar.extractall(REPO)
        for name in ('requirements.lock.txt', 'requirements.in'):
            for hit in glob.glob(f'/kaggle/input/**/e1-oec-code/**/{name}', recursive=True):
                shutil.copy(hit, REPO / name)
    (REPO / 'logs').mkdir(exist_ok=True)
    return link_assets()


def link_assets():
    """Wire the competition's snapshots, graphs, embeddings and shared wheels into the repo layout.

    Per-task wheel caches from the assets dataset (21 tasks, proven) win; every other task falls back
    to the competition's shared wheels directory. Which tasks really work is decided by the preflight."""
    comp = next((Path(p).parent for p in sorted(glob.glob('/kaggle/input/**/snapshots', recursive=True), key=len)
                 if (Path(p).parent / 'tasks.jsonl').is_file()), None)
    if comp is None:
        raise SystemExit('competition data not mounted (no snapshots/ + tasks.jsonl)')
    log('competition_root', str(comp))
    dest = REPO / 'data/assets'
    for sub in ('snapshots', 'graph', 'embeddings', 'task_wheels'):
        shutil.rmtree(dest / sub, ignore_errors=True)
        (dest / sub).mkdir(parents=True, exist_ok=True)
    for path in (comp / 'graphs').iterdir():
        (dest / 'graph' / path.name).symlink_to(path)
    for path in (comp / 'embeddings').iterdir():
        (dest / 'embeddings' / path.name).symlink_to(path)
    own = {}
    hits = sorted(glob.glob('/kaggle/input/**/e1-oec-assets/task_ids.json', recursive=True), key=len)
    if hits:
        root = Path(hits[0]).parent
        own = {t: root / 'task_wheels' / t for t in json.loads(Path(hits[0]).read_text())['tasks']}
    log('own_wheel_caches', len(own))
    verified = [l.strip() for l in (REPO / 'docs/reports/reference_patch_passed_task_ids.txt').read_text().splitlines()
                if l.strip()]
    TASK_IDS[:] = [t for t in verified if (comp / 'snapshots' / f'{t}.tgz').is_file()]
    log('verified_tasks', len(verified))
    log('tasks_with_snapshots', len(TASK_IDS))
    shared = comp / 'wheels'
    for task in TASK_IDS:
        (dest / 'snapshots' / f'{task}.tgz').symlink_to(comp / 'snapshots' / f'{task}.tgz')
        (dest / 'task_wheels' / task).symlink_to(own.get(task, shared))
    return shared


def run_preflight(shared):
    """Start the reference-patch preflight in the background (CPU only); returns the Popen."""
    cmd = [str(PY313), 'scripts/runs/preflight_reference.py', '--task-ids', *TASK_IDS,
           '--concurrency', '4', '--wheels-dir-for-all', str(shared), '--out', str(WORKING / 'preflight.json')]
    env = {**os.environ, 'PYTHONUNBUFFERED': '1'}
    env.pop('PYTHONPATH', None)
    stage('preflight: ' + ' '.join(map(str, cmd[1:5])) + ' ...')
    return subprocess.Popen(cmd, cwd=REPO, env=env, stdout=(WORKING / 'preflight_console.log').open('w'),
                            stderr=subprocess.STDOUT, text=True)


def finish_preflight(proc):
    code = proc.wait(timeout=3 * 3600)
    log('preflight_exit', code)
    result = json.loads((WORKING / 'preflight.json').read_text())
    passed = [t for t in TASK_IDS if t in set(result['passed'])]
    log('preflight_passed', len(passed))
    log('preflight_failed', {t: r[:120] for t, r in list(result['failed'].items())[:200]})
    TASK_IDS[:] = passed
    if not passed:
        raise SystemExit('preflight: no task could be set up with the available wheels')


def install_py313_harness():
    """Extract the prebuilt 3.13 harness env to its original absolute paths (no network)."""
    hits = sorted(glob.glob('/kaggle/input/**/h313.bin', recursive=True))
    log('env_bundle', hits)
    if not hits:
        raise SystemExit('h313.bin not mounted: attach dataset akshayggupta1/e1-oec-py313-env')
    if run(['tar', 'xzf', hits[0], '-C', '/']).returncode:
        raise SystemExit('extracting h313.bin failed')
    check = ("import sys; sys.path[:0] = ['scripts/runs', 'scripts/traces']; "
             "import task_pipeline, run_oec, handoff_llm, handoff_snapshot; from importlib import metadata; "
             "print('IMPORT_OK', sys.version.split()[0], metadata.version('swegemma'), "
             "metadata.version('adk_submission'), metadata.version('google-adk'), metadata.version('litellm'))")
    result = run([PY313, '-c', check], cwd=REPO)
    if result.returncode:
        raise SystemExit('task_pipeline does not import under the extracted 3.13 env')
    log('harness313', result.stdout.strip())


def install_wheelhouse():
    """Same steps as notebooks/starter.ipynb cell 1, into the image's Python 3.12."""
    wheelhouse = next((d for d in WHEELHOUSE_DIRS if d.is_dir()), None)
    if wheelhouse is None:
        raise SystemExit('competition wheelhouse not mounted')
    for pattern in ('/usr/local/lib/python*/dist-packages/*cutlass*.pth',
                    '/usr/local/lib/python*/site-packages/*cutlass*.pth'):
        for pth in glob.glob(pattern):
            try:
                os.unlink(pth)
            except OSError:
                pass
    tmp_whl = Path('/tmp/wheelhouse')
    tmp_whl.mkdir(parents=True, exist_ok=True)
    for w in wheelhouse.glob('*.whl'):
        if 'cutlass' in w.name.lower():
            continue
        name = w.name.replace('cu128', '+cu128') if ('cu128' in w.name and '+' not in w.name) else w.name
        target = tmp_whl / name
        if not target.exists():
            os.symlink(w, target)
    wheels = sorted(str(w) for w in tmp_whl.glob('*.whl'))
    stage(f'installing {len(wheels)} wheels from {wheelhouse}')
    rc = subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', '--no-deps',
                         '--force-reinstall', *wheels]).returncode
    importlib.invalidate_caches()
    log('wheelhouse_install_rc', rc)
    if rc:
        raise SystemExit('wheelhouse install failed')


def find_model_dir():
    hits = [os.path.dirname(p) for p in glob.glob('/kaggle/input/**/config.json', recursive=True)
            if MODEL_INSTANCE in p]
    if not hits:
        raise SystemExit(f'{MODEL_INSTANCE} not mounted')
    return sorted(hits, key=len)[0]


def gpu_count():
    import torch
    return max(1, torch.cuda.device_count())


def start_vllm(model_dir):
    """Starter cell 8 VllmConfig, E4B base, no LoRA: one independent server per GPU, served as gemma4.

    Server i listens on PORT + i and sees only GPU i, so N L4s give N data-parallel replicas
    (E4B fits one L4; tensor parallelism would only add communication)."""
    import torch
    from adk_submission import VllmConfig, VllmServer

    class PinnedVllmServer(VllmServer):
        def __init__(self, config, gpu):
            super().__init__(config)
            self.gpu = gpu

        def build_env(self):
            env = super().build_env()
            env['CUDA_VISIBLE_DEVICES'] = str(self.gpu)
            return env

    count = gpu_count()
    log('gpu_count', count)
    servers = []
    for gpu in range(count):
        cfg = VllmConfig(
            model=model_dir, port=PORT + gpu, host='127.0.0.1',
            tool_call_parser='gemma4', reasoning_parser='gemma4',
            default_chat_template_kwargs={'enable_thinking': True},
            max_model_len=32768,
            dtype='bfloat16' if (torch.cuda.is_available() and torch.cuda.is_bf16_supported()) else 'auto',
            gpu_memory_utilization=0.90, enable_auto_tool_choice=True,
            enable_lora=False, tensor_parallel_size=1, startup_timeout=60 * 20,
            extra_args=['--served-model-name', SERVED_NAME],
        )
        servers.append(PinnedVllmServer(cfg, gpu))
    log('vllm_cmd', servers[0].build_cmd())
    t0 = time.time()
    failures = []

    def boot(server):
        try:
            server.start()
        except Exception as error:  # noqa: BLE001
            failures.append((server.gpu, f'{type(error).__name__}: {error}'))
            log_path = getattr(server, 'log_path', None)
            if log_path and Path(log_path).is_file():
                print(f'--- gpu {server.gpu} vLLM log tail ---')
                print(Path(log_path).read_text()[-6000:], flush=True)

    threads = [threading.Thread(target=boot, args=(server,)) for server in servers]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    log('vllm_start_seconds', round(time.time() - t0, 1))
    if failures:
        log('vllm_start', f'FAILED {failures}')
        for server in servers:
            server.stop()
        raise SystemExit(f'vLLM failed to start on GPUs {[g for g, _ in failures]}')
    for server in servers:
        base = f'http://127.0.0.1:{server.config.port}/v1'
        with urllib.request.urlopen(f'{base}/models', timeout=10) as response:
            served = [m['id'] for m in json.loads(response.read())['data']]
        if served != [SERVED_NAME]:
            raise SystemExit(f'expected only {SERVED_NAME!r} served on {base}, got {served}')
    log('served_models', SERVED_NAME)
    return servers


def run_generation(servers):
    bases = [f'http://127.0.0.1:{server.config.port}/v1' for server in servers]
    cmd = ['scripts/runs/run_oec.py', '--run-prefix', RUN_PREFIX, '--attempts', str(ATTEMPTS),
           '--workers', str(len(bases)), '--api-base', *bases,
           '--switch-low', str(SWITCH_LOW), '--switch-high', str(SWITCH_HIGH),
           '--task-ids', *TASK_IDS, '--sandbox', 'subprocess', '--stage', 'student',
           '--temperature', str(TEMPERATURE)]
    log('run_oec_command', cmd)
    env = {**os.environ, 'VLLM_API_KEY': 'EMPTY', 'PYTHONUNBUFFERED': '1'}
    env.pop('PYTHONPATH', None)
    console = WORKING / 'run_oec_console.log'
    stage('run_oec.py ' + ' '.join(cmd[1:]))
    t0 = time.time()
    with console.open('w') as out:
        proc = subprocess.Popen([str(PY313), *cmd], cwd=REPO, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)
        for line in proc.stdout:
            out.write(line)
            print(line, end='', flush=True)
        code = proc.wait(timeout=3 * 3600)
    log('run_oec_exit', code)
    log('run_oec_minutes', round((time.time() - t0) / 60, 1))


def redact_tree(root):
    if not SECRET['value'] or not root.exists():
        return
    for path in root.rglob('*'):
        if path.is_file():
            try:
                text = path.read_text(encoding='utf-8')
            except (UnicodeDecodeError, OSError):
                continue
            if SECRET['value'] in text:
                path.write_text(redact(text), encoding='utf-8')
                log(f'redacted_{path.name}', True)


def gpu_name():
    result = subprocess.run(['nvidia-smi', '--query-gpu=name', '--format=csv,noheader'],
                            capture_output=True, text=True)
    return (result.stdout or result.stderr).strip()


def count_snapshots(base):
    """Snapshot directories under every attempt of this run, by attempt: {'a1': n, ...}."""
    counts = {}
    for attempt in sorted(base.glob(f'{RUN_PREFIX}-a*')) if base.exists() else []:
        counts[attempt.name.rsplit('-', 1)[-1]] = sum(
            1 for snap in attempt.glob('*/*/snapshot') if (snap / 'manifest.json').is_file())
    return counts


def collect():
    base = REPO / 'logs/remote/rishav/inspiration-one'
    consoles = REPO / 'logs/oec_runs' / RUN_PREFIX
    # EVERY attempt directory (oec-v1-a1 ... a<N>), not just the first: one per attempt.
    sources = sorted(base.glob(f'{RUN_PREFIX}-a*')) if base.exists() else []
    for src in [*sources, consoles]:
        if src.exists():
            shutil.copytree(src, WORKING / src.relative_to(REPO), dirs_exist_ok=True)
    produced, saved = count_snapshots(base), count_snapshots(WORKING / 'logs/remote/rishav/inspiration-one')
    log('snapshots_produced_by_attempt', produced)
    log('snapshots_saved_by_attempt', saved)
    print(f'snapshots produced {produced}; saved to output {saved}', flush=True)
    if produced != saved:
        raise SystemExit(f'LOST SNAPSHOTS: produced {produced} but saved {saved}')
    for path in sorted(consoles.glob('*.log')) if consoles.exists() else []:
        print(f'\n===== console {path.name} (tail) =====')
        print(path.read_text(errors='replace')[-4000:], flush=True)
    good, bad = [], []
    saved_root = WORKING / 'logs/remote/rishav/inspiration-one'
    for snap in sorted(saved_root.glob(f'{RUN_PREFIX}-a*/*/*/snapshot')) if saved_root.exists() else []:
        try:
            handoff = json.loads((snap / 'handoff.json').read_text())
            responses = json.loads((snap / 'student_responses.json').read_text())
            events = json.loads((snap / 'session_events.json').read_text())
            exchanges = json.loads((snap / 'vllm_exchanges.json').read_text())
            json.loads((snap / 'manifest.json').read_text())
            ok = (len(responses) == handoff['switch_after'] == handoff['student_calls']
                  and len(events) > 0 and len(exchanges) >= len(responses))
        except Exception as error:  # noqa: BLE001
            ok, handoff = False, {'error': f'{type(error).__name__}: {error}'}
        (good if ok else bad).append('/'.join(snap.parts[-4:-1]))
        if not ok:
            print('BAD SNAPSHOT', snap, {k: handoff.get(k) for k in ('switch_after', 'student_calls', 'error')},
                  flush=True)
    log('snapshots_well_formed', len(good))
    log('snapshots_bad', bad)
    print(f'well-formed snapshots: {len(good)} of {sum(saved.values())} saved; bad: {len(bad)}', flush=True)


def main():
    WORKING.mkdir(parents=True, exist_ok=True)
    log('python', sys.version.split()[0])
    log('accelerator', gpu_name())
    (WORKING / 'accelerator.txt').write_text(REPORT['accelerator'] + '\n')
    shared = stage_repo()
    model_dir = find_model_dir()
    log('model_dir', model_dir)
    log('run_config', {'temperature': TEMPERATURE, 'attempts': ATTEMPTS, 'switch_low': SWITCH_LOW, 'switch_high': SWITCH_HIGH,
                       'sandbox': 'subprocess', 'stage': 'student',
                       'student': f'{MODEL_INSTANCE} base, served as {SERVED_NAME}, no LoRA',
                       'servers': 'one per GPU, ports PORT+i'})
    (WORKING / 'run_config.json').write_text(json.dumps(REPORT['run_config'], indent=2))
    install_py313_harness()
    preflight = run_preflight(shared)
    install_wheelhouse()
    servers = start_vllm(model_dir)
    finish_preflight(preflight)
    try:
        run_generation(servers)
    finally:
        try:
            collect()
        finally:
            for server in servers:
                server.stop()
            write_report()
    print('GEN_STUDENT_DONE', flush=True)


if __name__ == '__main__':
    try:
        main()
    finally:
        write_report()
