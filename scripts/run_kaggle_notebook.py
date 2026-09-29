"""Push the competition notebook to Kaggle and follow its live logs."""
import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
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


def stream_to_log(kernel, env, log_path):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8", buffering=1) as log_file:
        process = subprocess.Popen(
            kaggle_command(["kernels", "logs", kernel, "--follow"], env),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        try:
            for line in process.stdout:
                log_file.write(line)
                print(line, end="", flush=True)
        except KeyboardInterrupt:
            process.terminate()
            raise
        return process.wait()


def tmux_session_name(run_name):
    suffix = re.sub(r"[^a-zA-Z0-9_-]+", "-", run_name).strip("-")
    return f"kaggle-{suffix}"[:80]


def run_log_path(kernel, version=None):
    """Return a unique per-run log path under logs/run."""
    notebook_name = re.sub(r"[^a-zA-Z0-9_-]+", "-", kernel.rsplit("/", 1)[-1]).strip("-")
    run_id = f"v{version}" if version else f"{time.strftime('%Y%m%dT%H%M%S', time.gmtime())}-{os.getpid()}"
    return ROOT / "logs" / "run" / f"{notebook_name}-{run_id}.txt"


def start_tmux_stream(kernel, log_path, session, token):
    fd, token_path = tempfile.mkstemp(prefix="kaggle-api-token-")
    os.chmod(token_path, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as token_file:
        token_file.write(token)

    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--_stream-worker",
        "--kernel",
        kernel,
        "--log-file",
        str(log_path),
        "--_token-file",
        token_path,
    ]
    try:
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", session, shlex.join(command)],
            cwd=ROOT,
            check=True,
        )
    except Exception:
        Path(token_path).unlink(missing_ok=True)
        raise
    return Path(token_path)


def follow_tmux_stream(kernel, log_path, session, interval, env, token_path=None):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.touch(exist_ok=True)
    tail = subprocess.Popen(
        ["tail", "-n", "+1", "-F", str(log_path)],
        stdout=sys.stdout,
        stderr=sys.stderr,
        text=True,
    )
    try:
        while True:
            if subprocess.run(["tmux", "has-session", "-t", session], capture_output=True).returncode:
                break
            result = cli(["kernels", "status", kernel], env)
            status_text = (result.stdout or result.stderr).strip()
            match = re.search(r"KernelWorkerStatus\.([A-Z_]+)", status_text)
            if match and match.group(1) in {"COMPLETE", "ERROR", "FAILED", "CANCELLED", "CANCEL_ACKNOWLEDGED", "ABORTED"}:
                # Let Kaggle's log follower flush its final output before returning.
                for _ in range(6):
                    if subprocess.run(["tmux", "has-session", "-t", session], capture_output=True).returncode:
                        break
                    time.sleep(1)
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        print("Stopped local log tail; Kaggle run and tmux log stream are still running.", flush=True)
        return False
    finally:
        tail.terminate()
        tail.wait()
        if token_path is not None and subprocess.run(
            ["tmux", "has-session", "-t", session], capture_output=True
        ).returncode:
            token_path.unlink(missing_ok=True)
    return True


def poll_run(kernel, env, interval, log_path):
    terminal = {"COMPLETE", "ERROR", "FAILED", "CANCELLED", "CANCEL_ACKNOWLEDGED", "ABORTED"}
    last_status = None
    streamed_logs = False

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

        if status in terminal:
            if not streamed_logs:
                logs = cli(["kernels", "logs", kernel], env)
                output = (logs.stdout or logs.stderr).strip()
                if output:
                    print(output, flush=True)
            return status

        streamed_logs = True
        session = tmux_session_name(log_path.stem)
        if shutil.which("tmux"):
            token_path = None
            if subprocess.run(["tmux", "has-session", "-t", session], capture_output=True).returncode:
                token_path = start_tmux_stream(kernel, log_path, session, env["KAGGLE_API_TOKEN"])
            print(f"Live logs: {log_path}", flush=True)
            if not follow_tmux_stream(kernel, log_path, session, interval, env, token_path):
                return None
        else:
            print(f"Live logs: {log_path} (tmux unavailable; streaming directly)", flush=True)
            stream_to_log(kernel, env, log_path)


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
    parser.add_argument("--_stream-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--log-file", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--_token-file", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()

    env = os.environ.copy()
    if args._stream_worker:
        if not args.kernel or not args.log_file:
            raise SystemExit("Internal stream worker requires --kernel and --log-file.")
        if args._token_file:
            try:
                env["KAGGLE_API_TOKEN"] = args._token_file.read_text(encoding="utf-8")
            finally:
                args._token_file.unlink(missing_ok=True)
        else:
            env["KAGGLE_API_TOKEN"] = load_token()
        raise SystemExit(stream_to_log(args.kernel, env, args.log_file))

    env["KAGGLE_API_TOKEN"] = load_token()

    metadata = json.loads(METADATA.read_text())
    kernel = args.kernel or metadata["id"]

    version = None
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
        version_match = re.search(r"Kernel version\s+(\d+)\s+successfully pushed", result.stdout or "")
        if version_match:
            version = version_match.group(1)

    log_path = run_log_path(kernel, version)
    final_status = poll_run(kernel, env, args.interval, log_path)
    print(f"Kaggle run finished with status {final_status}.", flush=True)
    if not args.skip_langfuse:
        push_to_langfuse(
            kaggle_kernel=kernel,
            session_id=args.langfuse_session_id,
            source_platform="kaggle",
        )


if __name__ == "__main__":
    main()
