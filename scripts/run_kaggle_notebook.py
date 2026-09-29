"""Push the competition notebook to Kaggle and follow its live logs."""
import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path

from langfuse_bridge import push_to_langfuse

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK_DIR = ROOT / "notebooks"
METADATA = NOTEBOOK_DIR / "kernel-metadata.json"
KAGGLE_CLI = "kaggle==2.2.4"


def load_token():
    """Load Kaggle's API token from the environment or the repository .env."""
    token = os.getenv("KAGGLE_API_TOKEN")
    if token:
        return token
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            key, separator, value = line.partition("=")
            if separator and key.strip() == "KAGGLE_API_TOKEN":
                return value.strip().strip("\"'")
    raise SystemExit("Set KAGGLE_API_TOKEN in the environment or root .env file.")


def kaggle_command(args, env):
    return ["uvx", "--from", KAGGLE_CLI, "kaggle", *args]


def enabled(value):
    return value is True or str(value).lower() == "true"


def cli(args, env):
    return subprocess.run(kaggle_command(args, env), env=env, capture_output=True, text=True)


def poll_run(kernel, env, interval):
    terminal = {"COMPLETE", "ERROR", "FAILED", "CANCELLED", "CANCEL_ACKNOWLEDGED", "ABORTED"}
    last_status = None
    seen_lines = set()

    while True:
        result = cli(["kernels", "status", kernel], env)
        status_text = (result.stdout or result.stderr).strip()
        match = re.search(r"KernelWorkerStatus\.([A-Z_]+)", status_text)
        if result.returncode or not match:
            print(f"Status unavailable; retrying in {interval}s. {status_text}", flush=True)
            time.sleep(interval)
            continue

        status = match.group(1)
        if status != last_status:
            print(f"Notebook status: {status}", flush=True)
            last_status = status

        if status == "QUEUED":
            time.sleep(interval)
            continue

        logs = cli(["kernels", "logs", kernel], env)
        output = logs.stdout or logs.stderr
        if output:
            new_lines = [line for line in output.splitlines() if line not in seen_lines]
            for line in new_lines:
                print(line, flush=True)
                seen_lines.add(line)
        elif logs.returncode:
            print(f"Log fetch failed; retrying in {interval}s.", flush=True)

        if status in terminal:
            return status
        time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--follow-only", action="store_true", help="Follow a run without pushing a new notebook version.")
    parser.add_argument("--kernel", help="Kaggle kernel handle; defaults to notebooks/kernel-metadata.json.")
    parser.add_argument("--interval", type=int, default=10, help="Live log polling interval in seconds.")
    parser.add_argument(
        "--skip-langfuse",
        action="store_true",
        help="Do not download and upload the completed run when Langfuse is configured.",
    )
    parser.add_argument(
        "--langfuse-session-id",
        help="Override the content-derived Langfuse session ID for this Kaggle run.",
    )
    args = parser.parse_args()

    env = os.environ.copy()
    env["KAGGLE_API_TOKEN"] = load_token()
    metadata = json.loads(METADATA.read_text())
    kernel = args.kernel or metadata["id"]

    if not args.follow_only:
        if not enabled(metadata.get("enable_gpu")) or enabled(metadata.get("enable_internet")):
            raise SystemExit("Notebook metadata must enable GPU and disable internet access.")
        result = subprocess.run(
            kaggle_command(
                ["kernels", "push", "-p", str(NOTEBOOK_DIR), "--accelerator", metadata["machine_shape"]],
                env,
            ),
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        print(result.stdout, end="", flush=True)
        if result.stderr:
            print(result.stderr, end="", flush=True)
        match = re.search(r"https://www\.kaggle\.com/code/([^\s]+)", result.stdout or "")
        if match:
            kernel = match.group(1).rstrip(".,")
            print(f"Following Kaggle run: {kernel}", flush=True)

    final_status = poll_run(kernel, env, args.interval)
    print(f"Kaggle run finished with status {final_status}.", flush=True)
    if not args.skip_langfuse:
        push_to_langfuse(
            kaggle_kernel=kernel,
            session_id=args.langfuse_session_id,
            source_platform="kaggle",
        )


if __name__ == "__main__":
    main()
