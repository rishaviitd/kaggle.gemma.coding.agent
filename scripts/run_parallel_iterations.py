#!/usr/bin/env python3
"""Run collaborator iterations two at a time.

Earlier entries in each person's list are queued before later ones.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / "bin" / "python"
PIPELINE = ROOT / "scripts" / "task_pipeline.py"


def job_order(plan: dict[str, list[str]]) -> list[tuple[str, str]]:
    """List (person, iteration) with each person's first item before any second."""
    names = list(plan)
    depth = max((len(iterations) for iterations in plan.values()), default=0)
    jobs: list[tuple[str, str]] = []
    for index in range(depth):
        for name in names:
            iterations = plan[name]
            if index < len(iterations):
                jobs.append((name, iterations[index]))
    return jobs


def next_job(
    pending: list[tuple[str, str]], running: dict[Path, tuple[str, str]]
) -> tuple[str, str] | None:
    del running
    return pending[0] if pending else None


def open_terminal(person: str, iteration: str, api_base: str, exit_path: Path, pid_path: Path) -> None:
    """Open a new Terminal window and record the pipeline exit code when it ends.

    Terminal keeps its own shell open after `do script`, so the exit code is
    written by a child script. The scheduler watches that child, not the window.
    """
    exit_path.parent.mkdir(parents=True, exist_ok=True)
    for path in (exit_path, pid_path, Path(str(exit_path) + ".tmp")):
        if path.exists():
            path.unlink()
    script_path = exit_path.with_suffix(".command")
    pipeline = " ".join(shlex.quote(part) for part in command(person, iteration, api_base))
    script_path.write_text(
        "#!/bin/zsh\n"
        "set +e\n"
        f"cd {shlex.quote(str(ROOT))} || exit 1\n"
        f"echo $$ > {shlex.quote(str(pid_path))}\n"
        f"{pipeline}\n"
        "pipeline_exit=$?\n"
        f"echo $pipeline_exit > {shlex.quote(str(exit_path))}.tmp && "
        f"mv {shlex.quote(str(exit_path))}.tmp {shlex.quote(str(exit_path))}\n"
        "exit $pipeline_exit\n",
        encoding="utf-8",
    )
    script_path.chmod(0o755)
    launch = f"/bin/zsh {shlex.quote(str(script_path))}"
    subprocess.run(
        ["osascript", "-e", f'tell application "Terminal" to do script {json.dumps(launch)}'],
        check=True,
    )


def exit_code(path: Path) -> int | None:
    """Return a finished exit code, ignoring a file that is still being written."""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return int(text) if text.isdigit() else None


def process_alive(pid_path: Path) -> bool | None:
    """Return whether the Terminal shell is still running. None if its pid is not written yet."""
    try:
        text = pid_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text.isdigit():
        return None
    try:
        os.kill(int(text), 0)
    except OSError:
        return False
    return True


def command(person: str, iteration: str, api_base: str) -> list[str]:
    return [
        str(PYTHON),
        str(PIPELINE),
        "--train",
        "--all",
        "--submission",
        f"src/{person}/{iteration}",
        "--api-base",
        api_base,
        "--skip-langfuse",
        "--resume",
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, help="JSON object of person to iteration list")
    parser.add_argument("--slots", type=int, default=2)
    parser.add_argument("--api-base", default="http://127.0.0.1:18001/v1")
    args = parser.parse_args()
    if args.slots < 1:
        raise SystemExit("--slots must be at least 1")

    plan = json.loads(args.plan)
    if not isinstance(plan, dict) or not plan:
        raise SystemExit("--plan must be a non-empty JSON object")
    for person, iterations in plan.items():
        if not isinstance(iterations, list) or not all(isinstance(item, str) for item in iterations):
            raise SystemExit(f"{person} must map to a list of iteration names")
        if len(iterations) != len(set(iterations)):
            raise SystemExit(f"{person} lists the same iteration more than once")
        submission = ROOT / "src" / person
        missing = [item for item in iterations if not (submission / item).is_dir()]
        if missing:
            raise SystemExit(f"Missing submission folders for {person}: {', '.join(missing)}")

    pending = job_order(plan)
    running: dict[Path, tuple[str, str]] = {}
    pid_paths: dict[Path, Path] = {}
    failures: list[tuple[str, str, int]] = []
    log_root = ROOT / "logs" / "scheduler"
    log_root.mkdir(parents=True, exist_ok=True)

    while pending or running:
        while len(running) < args.slots:
            job = next_job(pending, running)
            if job is None:
                break
            person, iteration = job
            run_dir = log_root / person
            exit_path = run_dir / f"{iteration}.exit"
            pid_path = run_dir / f"{iteration}.pid"
            try:
                open_terminal(person, iteration, args.api_base, exit_path, pid_path)
            except (OSError, subprocess.CalledProcessError) as error:
                pending.remove(job)
                failures.append((person, iteration, 1))
                print(f"failed to open Terminal for {person}/{iteration}: {error}", flush=True)
                continue
            pending.remove(job)
            running[exit_path] = job
            pid_paths[exit_path] = pid_path
            print(f"started {person}/{iteration} in a new Terminal window", flush=True)

        if not running:
            break
        finished: tuple[Path, int] | None = None
        while finished is None:
            for exit_path in list(running):
                code = exit_code(exit_path)
                if code is not None:
                    finished = (exit_path, code)
                    break
                if process_alive(pid_paths[exit_path]) is False:
                    time.sleep(2)
                    code = exit_code(exit_path)
                    finished = (exit_path, 1 if code is None else code)
                    break
            else:
                time.sleep(5)
        exit_path, code = finished
        person, iteration = running.pop(exit_path)
        pid_paths.pop(exit_path, None)
        if code:
            failures.append((person, iteration, code))
            print(f"failed {person}/{iteration} exit={code}", flush=True)
        else:
            print(f"finished {person}/{iteration}", flush=True)

    if failures:
        for person, iteration, code in failures:
            print(f"FAILED {person}/{iteration} exit={code}", file=sys.stderr)
        raise SystemExit(1)
    print("all runs finished")


if __name__ == "__main__":
    main()
