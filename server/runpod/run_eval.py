"""Run itr-1 val tasks against RunPod E4B (eval + throughput benchmark).

Uses submission sampling/limits from src/rishav/itr-1 unless you pass CLI
overrides via task_pipeline. Parallelism = number of worker processes
(EVAL_WORKERS), each running one task at a time against the shared vLLM.
"""
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "scripts/task_pipeline.py").is_file())
DEFAULT_TASKS = """
fastapi_12942 fastapi_14372 fastapi_14794 fastapi_5624 fastapi_14077
fastapi_14266 fastapi_14246 fastapi_14482 fastapi_14419 fastapi_14306
rich_3043 rich_3905 rich_4076 rich_3506 rich_3935 rich_3052 rich_3180
requests_7205 requests_7427 httpx_3672
""".split()
API = os.environ.get(
    "RUNPOD_API_BASE",
    "https://t4r30ysyfmt164-8000.proxy.runpod.net/v1",
)
WORKERS = int(os.environ.get("EVAL_WORKERS", "8"))
RUN_NAME = os.environ.get("BENCHMARK_RUN_NAME", "e4b-benchmark")
SUBMISSION = os.environ.get("BENCHMARK_SUBMISSION", "src/rishav/itr-1")


def main():
    load_dotenv(ROOT / ".env")
    tasks = sys.argv[1:] or DEFAULT_TASKS
    workers = min(WORKERS, len(tasks))
    log_dir = ROOT / "logs" / "remote" / Path(SUBMISSION).relative_to("src") / RUN_NAME
    log_dir.mkdir(parents=True, exist_ok=True)
    print(f"RunPod benchmark: {len(tasks)} tasks, {workers} workers, run={RUN_NAME}", flush=True)
    print(f"API: {API}", flush=True)
    print(f"Submission: {SUBMISSION}", flush=True)
    print(f"Logs: logs/remote/{Path(SUBMISSION).relative_to('src')}/{RUN_NAME}/val/", flush=True)
    processes = []
    for index in range(workers):
        chunk = tasks[index::workers]
        if not chunk:
            continue
        log = log_dir / f"worker-{index + 1}.log"
        file = log.open("a")
        cmd = [
            sys.executable, "-u", str(ROOT / "scripts/task_pipeline.py"),
            "--val", "--task-ids", *chunk,
            "--submission", SUBMISSION,
            "--run-name", RUN_NAME,
            "--provider", "vllm", "--model", "gemma4",
            "--api-base", API,
            "--skip-langfuse", "--resume",
        ]
        processes.append(subprocess.Popen(cmd, cwd=ROOT, stdout=file, stderr=subprocess.STDOUT))
        file.close()
    raise SystemExit(max(process.wait() for process in processes))


if __name__ == "__main__":
    main()
