"""Rebuild a Langfuse run with structured ATIF steps at the run-root level.

Example:
  uv run --with 'langfuse>=4,<5' --python 3.12 python scripts/stitch_langfuse_trace.py \
    --artifact-dir /tmp/kaggle-run-output-353660769

  uv run --with 'langfuse>=4,<5' --python 3.12 python scripts/stitch_langfuse_trace.py \
    --kaggle-kernel owner/notebook-slug

The Kaggle notebook stays offline. This script runs locally after downloading its
output artifacts. It validates every ATIF file before upload, refuses accidental
duplicate sessions, and verifies the uploaded hierarchy before optionally deleting
old traces supplied with --delete-trace-id. With --kaggle-kernel it downloads
the notebook's latest output first. Console-log upload is opt-in.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from langfuse import Langfuse, propagate_attributes


ROOT = Path(__file__).resolve().parents[1]
LANGFUSE_KEYS = {
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_BASE_URL",
}
ENV_KEYS = LANGFUSE_KEYS | {"KAGGLE_API_TOKEN"}
KAGGLE_CLI = "kaggle==2.2.4"


def load_langfuse_env(env_path: Path) -> None:
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("export "):
                stripped = stripped[7:].lstrip()
            key, separator, value = stripped.partition("=")
            key = key.strip()
            if separator and key in ENV_KEYS and not os.getenv(key):
                os.environ[key] = value.strip().strip("\"'")
    missing = sorted(key for key in LANGFUSE_KEYS if not os.getenv(key))
    if missing:
        source = str(env_path) if env_path.is_file() else "the process environment"
        raise ValueError(f"Missing Langfuse settings in {source}: " + ", ".join(missing))
    placeholders = sorted(
        key for key in LANGFUSE_KEYS
        if "your-" in os.environ[key].lower() or os.environ[key].strip() in {'""', "''"}
    )
    if placeholders:
        raise ValueError(
            "Replace placeholder Langfuse settings in "
            f"{env_path}: {', '.join(placeholders)}"
        )


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--artifact-dir",
        type=Path,
        help="Existing Kaggle output root or local evaluator results directory",
    )
    source.add_argument(
        "--kaggle-kernel",
        help="Kaggle notebook handle (owner/slug); downloads its latest run output before import",
    )
    parser.add_argument(
        "--download-dir",
        type=Path,
        help="Directory for --kaggle-kernel output; defaults to a new directory under /tmp",
    )
    parser.add_argument(
        "--task-id",
        action="append",
        default=[],
        help="Import only this task ID; repeat to select multiple tasks",
    )
    parser.add_argument(
        "--source-platform",
        choices=("kaggle", "vllm", "local"),
        help="Run source label; defaults to kaggle when omitted",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=ROOT / ".env",
        help="File containing LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, and LANGFUSE_BASE_URL",
    )
    parser.add_argument(
        "--session-id",
        help="Langfuse session ID; defaults to <source-platform>-run-<artifact label>",
    )
    parser.add_argument(
        "--include-console-log",
        action="store_true",
        help="Upload the notebook console log as an observation (disabled by default)",
    )
    parser.add_argument(
        "--delete-trace-id",
        action="append",
        default=[],
        help="Existing Langfuse trace ID to delete only after the replacement is verified; repeat as needed",
    )
    parser.add_argument(
        "--kernel-log",
        type=Path,
        help="Notebook-level Kaggle log; defaults to the first .log in artifact-dir",
    )
    return parser.parse_args()


def observation_dict(row: Any) -> dict[str, Any]:
    if hasattr(row, "model_dump"):
        return row.model_dump()
    return dict(row)


def find_existing_observations(client: Any, trace_id: str) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    response = client.api.observations.get_many(
        trace_id=trace_id,
        from_start_time=now - timedelta(days=30),
        to_start_time=now + timedelta(minutes=1),
        limit=1000,
        fields="core,basic,metadata",
    )
    return [observation_dict(row) for row in response.data]


def find_session_root_trace_ids(client: Any, session_id: str) -> set[str]:
    now = datetime.now(timezone.utc)
    response = client.api.observations.get_many(
        session_id=session_id,
        is_root_observation=True,
        from_start_time=now - timedelta(days=30),
        to_start_time=now + timedelta(minutes=1),
        limit=1000,
        fields="core,basic",
    )
    return {
        str(first_value(observation_dict(row), "trace_id", "traceId"))
        for row in response.data
        if first_value(observation_dict(row), "trace_id", "traceId")
    }


def first_value(record: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if record.get(key) is not None:
            return record[key]
    return None


def load_atif_trace(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"ATIF trace must be a JSON object: {path}")
    if data.get("schema_version") != "ATIF-v1.7":
        raise ValueError(
            f"Unsupported ATIF schema in {path}: {data.get('schema_version')!r}; expected 'ATIF-v1.7'"
        )
    steps = data.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError(f"ATIF trace has no non-empty steps list: {path}")
    if not all(isinstance(step, dict) for step in steps):
        raise ValueError(f"Every ATIF step must be an object: {path}")
    agent_steps = [step for step in steps if step.get("source") == "agent"]
    if not agent_steps:
        raise ValueError(f"ATIF trace contains no agent steps: {path}")
    step_ids = [step.get("step_id") for step in agent_steps]
    if not all(isinstance(step_id, int) and step_id >= 0 for step_id in step_ids):
        raise ValueError(f"Every agent step needs a non-negative integer step_id: {path}")
    if len(step_ids) != len(set(step_ids)):
        raise ValueError(f"Agent step_id values must be unique: {path}")
    for step in agent_steps:
        calls = step.get("tool_calls") or []
        if not isinstance(calls, list) or not all(isinstance(call, dict) for call in calls):
            raise ValueError(f"tool_calls must be a list of objects in agent step {step['step_id']}: {path}")
    return data


def validate_delete_trace_ids(trace_ids: list[str]) -> list[str]:
    invalid = [trace_id for trace_id in trace_ids if not re.fullmatch(r"[0-9a-f]{32}", trace_id)]
    if invalid:
        raise ValueError(
            "Invalid --delete-trace-id value(s); expected 32 lowercase hexadecimal characters: "
            + ", ".join(invalid)
        )
    return list(dict.fromkeys(trace_ids))


def download_kaggle_output(kernel: str, destination: Path | None) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", kernel):
        raise ValueError("--kaggle-kernel must use the owner/notebook-slug format")
    token = os.getenv("KAGGLE_API_TOKEN")
    if not token:
        raise ValueError("Set KAGGLE_API_TOKEN in the environment or --env-file before downloading")
    if destination is None:
        destination = Path(tempfile.mkdtemp(prefix="kaggle-run-output-"))
    else:
        destination = destination.expanduser().resolve()
        if destination.exists():
            if not destination.is_dir():
                raise ValueError(f"Download path is not a directory: {destination}")
            if any(destination.iterdir()):
                raise ValueError(f"Download directory must be empty: {destination}")
        destination.mkdir(parents=True, exist_ok=True)

    command = [
        "uvx", "--from", KAGGLE_CLI, "kaggle", "kernels", "output",
        kernel, "--path", str(destination), "--force",
    ]
    result = subprocess.run(
        command,
        env={**os.environ, "KAGGLE_API_TOKEN": token},
        text=True,
        capture_output=True,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout or "unknown Kaggle CLI error").strip()
        raise RuntimeError(f"Kaggle output download failed: {detail[-2000:]}")
    print(f"Downloaded latest Kaggle output for {kernel} to {destination}", flush=True)
    return destination


def artifact_fingerprint(traces: list[tuple[Path, dict[str, Any]]]) -> str:
    digest = hashlib.sha256()
    for trace_path, _ in traces:
        digest.update(trace_path.name.encode("utf-8"))
        digest.update(trace_path.read_bytes())
    return digest.hexdigest()[:12]


def find_trace_directory(artifact_dir: Path) -> Path:
    """Accept a Kaggle output root or a local evaluator results directory."""
    candidates = (artifact_dir / "results" / "traces", artifact_dir / "traces")
    return next((path for path in candidates if path.is_dir()), candidates[0])


def parse_console_log(path: Path) -> list[dict[str, Any]]:
    """Read Kaggle's downloaded JSON event stream or a plain-text log."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    try:
        events = json.loads(raw)
    except json.JSONDecodeError:
        return [{"stream": "stdout", "time": None, "text": raw}]
    if not isinstance(events, list):
        return [{"stream": "stdout", "time": None, "text": raw}]
    return [
        {
            "stream": item.get("stream_name", "stdout"),
            "time": item.get("time"),
            "text": item.get("data", ""),
        }
        for item in events
        if isinstance(item, dict)
    ]


def task_result_from_log(task_id: str, events: list[dict[str, Any]]) -> dict[str, Any] | None:
    text = "\n".join(str(event.get("text", "")) for event in events)
    pattern = re.compile(
        rf"Evaluating\s+{re.escape(task_id)}\b.*?"
        r"->\s*resolved=(True|False),\s*"
        r"exit_code=(-?\d+),\s*patch_chars=(\d+),\s*"
        r"tool_calls=(\d+),\s*duration=([\d.]+)s",
        re.DOTALL,
    )
    match = pattern.search(text)
    if not match:
        return None
    return {
        "task_id": task_id,
        "resolved": match.group(1) == "True",
        "test_exit_code": int(match.group(2)),
        "patch_chars": int(match.group(3)),
        "tool_calls": int(match.group(4)),
        "duration_seconds": float(match.group(5)),
    }


def task_result_from_artifacts(
    task_id: str,
    artifact_dir: Path,
    events: list[dict[str, Any]],
) -> dict[str, Any] | None:
    from_log = task_result_from_log(task_id, events)
    if from_log is not None:
        return from_log

    direct_results = [
        artifact_dir / f"{task_id}.json",
        artifact_dir / "results" / f"{task_id}.json",
    ]
    direct_result = next((path for path in direct_results if path.is_file()), direct_results[0])
    candidates: list[dict[str, Any]] = []
    if direct_result.is_file():
        try:
            value = json.loads(direct_result.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid task result JSON in {direct_result}: {exc}") from exc
        if isinstance(value, dict):
            candidates.append(value)

    jsonl_results = [
        artifact_dir / "task_results.jsonl",
        artifact_dir / "results" / "task_results.jsonl",
    ]
    jsonl_result = next((path for path in jsonl_results if path.is_file()), jsonl_results[0])
    if jsonl_result.is_file():
        for line_number, line in enumerate(jsonl_result.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {line_number} of {jsonl_result}: {exc}") from exc
            if isinstance(value, dict):
                candidates.append(value)

    row = next(
        (
            candidate for candidate in candidates
            if candidate.get("task_id", candidate.get("instance_id")) == task_id
        ),
        None,
    )
    if row is None or "resolved" not in row:
        return None
    patch = row.get("agent_patch")
    return {
        "task_id": task_id,
        "resolved": bool(row["resolved"]),
        "test_exit_code": int(row.get("test_exit_code", -1)),
        "patch_chars": len(patch) if isinstance(patch, str) else int(row.get("agent_patch_size", 0)),
        "tool_calls": int(row.get("tool_calls", 0)),
        "duration_seconds": float(row.get("duration_seconds", 0.0)),
        "status": row.get("status"),
        "error_message": row.get("error_message", row.get("error")),
        "result_source": direct_result.name if direct_result.is_file() else jsonl_result.name,
    }


def metadata_for_step(step: dict[str, Any]) -> dict[str, str]:
    extra = step.get("extra") or {}
    return {
        "sourceFormat": "ATIF-v1.7",
        "atifStepId": str(step.get("step_id", "")),
        "atifAuthor": str(extra.get("author", "")),
        "atifEventType": str(extra.get("event_type", "")),
        "atifOriginalElapsedSeconds": str(extra.get("elapsed_s", "")),
        "atifOriginalTimestamp": str(extra.get("timestamp", "")),
        "timingNote": "Original ATIF timing is metadata; import-time span duration is not task latency.",
    }


def stitch_task(
    client: Any,
    *,
    trace_id: str,
    root_observation_id: str,
    trace_path: Path,
    trace_data: dict[str, Any],
    task_result: dict[str, Any] | None,
) -> tuple[int, int]:
    steps = trace_data.get("steps", [])
    root_context = {"trace_id": trace_id, "parent_span_id": root_observation_id}
    system_messages = [
        step.get("message", "") for step in steps if step.get("source") == "system"
    ]
    user_messages = [
        step.get("message", "") for step in steps if step.get("source") == "user"
    ]
    task_id = trace_path.stem.removeprefix("trace_")
    task_context = client.start_observation(
        trace_context=root_context,
        name=f"task-context-{task_id}",
        as_type="span",
        input={"system_messages": system_messages, "user_messages": user_messages},
        metadata={"taskId": task_id, "sourceFormat": "ATIF-v1.7"},
    )
    task_context.end()

    history = [
        {"role": "system", "content": message} for message in system_messages
    ] + [
        {"role": "user", "content": message} for message in user_messages
    ]

    agent_steps = 0
    tool_calls_total = 0
    for step in steps:
        if step.get("source") != "agent":
            continue
        agent_steps += 1
        step_id = step.get("step_id", agent_steps)
        # Langfuse stores timestamps at millisecond precision. Separate imported
        # turns so the UI's chronological tree order follows the ATIF sequence.
        time.sleep(0.02)
        metrics = step.get("metrics") or {}
        usage = {
            key: int(metrics[value])
            for key, value in (
                ("input", "prompt_tokens"),
                ("output", "completion_tokens"),
                ("total", "total_tokens"),
            )
            if metrics.get(value) is not None
        }
        generation = client.start_observation(
            trace_context=root_context,
            name=f"agent-step-{int(step_id):03d}",
            as_type="generation",
            input={"messages": history},
            model=step.get("model_name"),
            output=step.get("message"),
            usage_details=usage or None,
            metadata=metadata_for_step(step),
        )
        step_observation = step.get("observation") or {}
        calls = step.get("tool_calls") or []
        history.append({"role": "assistant", "content": step.get("message", ""), "tool_calls": calls})
        for call_index, call in enumerate(calls, start=1):
            tool_calls_total += 1
            call_extra = call.get("extra") or {}
            observation_extra = step_observation.get("extra") or {}
            tool_metadata = {
                "atifStepId": str(step_id),
                "toolCallId": str(call.get("tool_call_id", "")),
                "toolName": str(call.get("function_name", "")),
                "atifCallElapsedSeconds": str(call_extra.get("elapsed_s", "")),
                "atifResultElapsedSeconds": str(observation_extra.get("elapsed_s", "")),
                "atifCallTimestamp": str(call_extra.get("timestamp", "")),
                "atifResultTimestamp": str(observation_extra.get("timestamp", "")),
            }
            tool_observation = generation.start_observation(
                name=f"tool-{call.get('function_name', 'unknown')}-{int(step_id):03d}-{call_index}",
                as_type="tool",
                input=call.get("arguments", {}),
                output=step_observation.get("content"),
                metadata=tool_metadata,
            )
            tool_observation.end()
            history.append({
                "role": "tool",
                "name": call.get("function_name", "unknown"),
                "tool_call_id": call.get("tool_call_id"),
                "content": step_observation.get("content"),
            })
        generation.end()

    if task_result is not None:
        result_observation = client.start_observation(
            trace_context=root_context,
            name=f"evaluation-result-{task_id}",
            as_type="evaluator",
            input={"task_id": task_id},
            output=task_result,
            metadata={
                "taskId": task_id,
                "resultSource": task_result.get("result_source", "notebook-console-log"),
            },
        )
        result_observation.end()
        client.create_score(
            trace_id=trace_id,
            observation_id=result_observation.id,
            name=f"resolved-{task_id}",
            value=1 if task_result["resolved"] else 0,
            data_type="NUMERIC",
            comment=(
                f"Resolved={task_result['resolved']}; exit_code={task_result['test_exit_code']}; "
                f"duration={task_result['duration_seconds']}s"
            ),
        )
    return agent_steps, tool_calls_total


def upload_run(
    client: Any,
    *,
    artifact_dir: Path,
    run_label: str,
    source_platform: str,
    session_id: str,
    traces: list[tuple[Path, dict[str, Any]]],
    log_path: Path | None,
    events: list[dict[str, Any]],
    include_console_log: bool,
) -> tuple[str, str, list[dict[str, Any]], int, int]:
    summaries: list[dict[str, Any]] = []
    total_agents = total_tools = 0
    run_name = (
        f"kaggle-notebook-run-{run_label}"
        if source_platform == "kaggle"
        else f"{source_platform}-agent-run-{run_label}"
    )
    with propagate_attributes(
        session_id=session_id,
        trace_name=run_name,
        tags=[source_platform, "agent-run", "offline-import"],
    ):
        run_root = client.start_observation(
            name=run_name,
            as_type="span",
            input={"source": f"{source_platform} output artifacts", "offline_import": True},
            metadata={"platform": source_platform, "runType": "agent-run", "source": "offline-import"},
        )
        try:
            if include_console_log and log_path is not None:
                log_child = run_root.start_observation(
                    name="kaggle-console-log",
                    as_type="span",
                    input={"filename": log_path.name, "events": events},
                )
                log_child.end()

            for trace_path, trace_data in traces:
                task_id = trace_path.stem.removeprefix("trace_")
                raw_child = run_root.start_observation(
                    name=f"raw-atif-trace-{task_id}",
                    as_type="span",
                    input={"filename": trace_path.name, "format": "ATIF-v1.7"},
                    output=trace_data,
                    metadata={"taskId": task_id, "sourceFormat": "ATIF-v1.7"},
                )
                raw_child.end()

                result = task_result_from_artifacts(task_id, artifact_dir, events)
                agents, tools = stitch_task(
                    client,
                    trace_id=run_root.trace_id,
                    root_observation_id=run_root.id,
                    trace_path=trace_path,
                    trace_data=trace_data,
                    task_result=result,
                )
                total_agents += agents
                total_tools += tools
                summaries.append({
                    "task_id": task_id,
                    "agent_steps": agents,
                    "tool_calls": tools,
                    "result_found": result is not None,
                })
        finally:
            run_root.end()

    client.flush()
    return run_root.trace_id, run_root.id, summaries, total_agents, total_tools


def main() -> None:
    args = arguments()
    if args.download_dir is not None and not args.kaggle_kernel:
        raise ValueError("--download-dir can only be used with --kaggle-kernel")
    args.env_file = args.env_file.expanduser().resolve()
    load_langfuse_env(args.env_file)
    if args.kaggle_kernel:
        if args.source_platform and args.source_platform != "kaggle":
            raise ValueError("--kaggle-kernel requires --source-platform kaggle")
        args.artifact_dir = download_kaggle_output(args.kaggle_kernel, args.download_dir)
    else:
        args.artifact_dir = args.artifact_dir.expanduser().resolve()
    if not args.artifact_dir.is_dir():
        raise FileNotFoundError(f"Artifact directory not found: {args.artifact_dir}")

    client = Langfuse(timeout=60)
    if not client.auth_check():
        raise SystemExit("Langfuse authentication failed")

    trace_dir = find_trace_directory(args.artifact_dir)
    trace_files = sorted(trace_dir.glob("trace_*.json"))
    if args.task_id:
        selected = set(args.task_id)
        trace_files = [
            path for path in trace_files
            if path.stem.removeprefix("trace_") in selected
        ]
        found = {path.stem.removeprefix("trace_") for path in trace_files}
        missing_tasks = selected - found
        if missing_tasks:
            raise FileNotFoundError(
                "No ATIF trace found for task(s): " + ", ".join(sorted(missing_tasks))
            )
    if not trace_files:
        raise FileNotFoundError(
            f"No ATIF trace JSON files under {args.artifact_dir}/results/traces "
            f"or {args.artifact_dir}/traces"
        )
    traces = [(trace_path, load_atif_trace(trace_path)) for trace_path in trace_files]

    log_path = args.kernel_log
    if log_path is not None:
        log_path = log_path.expanduser().resolve()
        if not log_path.is_file():
            raise FileNotFoundError(f"Notebook log not found: {log_path}")
    else:
        candidates = sorted(args.artifact_dir.glob("*.log"))
        candidates.extend(sorted((args.artifact_dir / "logs").glob("*.log")))
        log_path = candidates[0] if candidates else None
    events = parse_console_log(log_path) if log_path is not None else []

    source_platform = args.source_platform or "kaggle"
    if args.kaggle_kernel:
        kernel_slug = args.kaggle_kernel.split("/", 1)[1]
        run_label = f"{kernel_slug}-{artifact_fingerprint(traces)}"
    else:
        run_label = args.artifact_dir.name.removeprefix("kaggle-run-output-")
    session_id = args.session_id or f"{source_platform}-run-{run_label}"
    if not session_id.strip() or len(session_id) > 200:
        raise ValueError("--session-id must contain 1 to 200 characters")
    delete_trace_ids = validate_delete_trace_ids(args.delete_trace_id)
    existing_session_traces = find_session_root_trace_ids(client, session_id)
    undeclared_existing = existing_session_traces - set(delete_trace_ids)
    if undeclared_existing:
        raise RuntimeError(
            f"Session {session_id!r} already contains trace(s): "
            f"{', '.join(sorted(undeclared_existing))}. Refusing a duplicate import. "
            "Pass each trace with --delete-trace-id to replace it, or choose a new --session-id."
        )

    run_trace_id, run_root_id, summaries, total_agents, total_tools = upload_run(
        client,
        artifact_dir=args.artifact_dir,
        run_label=run_label,
        source_platform=source_platform,
        session_id=session_id,
        traces=traces,
        log_path=log_path,
        events=events,
        include_console_log=args.include_console_log,
    )

    replacement: list[dict[str, Any]] = []
    root_children: list[dict[str, Any]] = []
    direct_step_count = nested_tool_count = 0
    verified = False
    last_read_error: Exception | None = None
    for attempt in range(8):
        try:
            replacement = find_existing_observations(client, run_trace_id)
            root_row = next((row for row in replacement if first_value(row, "id") == run_root_id), None)
            root_children = [
                row for row in replacement
                if first_value(row, "parent_observation_id", "parentObservationId") == run_root_id
            ]
            step_rows = [
                row for row in root_children
                if str(first_value(row, "name", "") or "").startswith("agent-step-")
            ]
            step_ids = {first_value(row, "id") for row in step_rows}
            direct_step_count = len(step_ids)
            nested_tool_count = sum(
                str(first_value(row, "name", "") or "").startswith("tool-")
                and first_value(row, "parent_observation_id", "parentObservationId") in step_ids
                for row in replacement
            )
            session_ids = {
                first_value(row, "session_id", "sessionId") for row in replacement
            }
            ordered_step_names = [
                str(first_value(row, "name"))
                for row in sorted(
                    step_rows,
                    key=lambda row: str(first_value(row, "start_time", "startTime") or ""),
                )
            ]
            expected_root_children = (
                total_agents
                + len(traces)  # raw ATIF observations
                + len(traces)  # task context observations
                + sum(bool(summary["result_found"]) for summary in summaries)
                + int(args.include_console_log and log_path is not None)
            )
            if (
                root_row
                and direct_step_count == total_agents
                and nested_tool_count == total_tools
                and len(root_children) == expected_root_children
                and session_ids == {session_id}
                and ordered_step_names == sorted(ordered_step_names)
            ):
                verified = True
                break
        except Exception as exc:
            last_read_error = exc
        if attempt < 7:
            time.sleep(3)
    if not verified:
        cleanup_status = "replacement cleanup not attempted"
        try:
            client.api.trace.delete(trace_id=run_trace_id)
            cleanup_status = "unverified replacement trace deleted"
        except Exception as cleanup_error:
            cleanup_status = f"replacement cleanup failed: {cleanup_error!r}"
        raise RuntimeError(
            f"Replacement hierarchy verification failed (steps={direct_step_count}/{total_agents}, "
            f"tools={nested_tool_count}/{total_tools}); old traces retained. "
            f"{cleanup_status}. Last read error: {last_read_error!r}"
        )

    deleted_trace_ids: list[str] = []
    missing_trace_ids: list[str] = []
    for trace_id in delete_trace_ids:
        if trace_id == run_trace_id:
            raise RuntimeError("Refusing to delete the verified replacement trace")
        if not find_existing_observations(client, trace_id):
            missing_trace_ids.append(trace_id)
            continue
        client.api.trace.delete(trace_id=trace_id)
        deleted_trace_ids.append(trace_id)
    print(json.dumps({
        "trace_id": run_trace_id,
        "session_id": session_id,
        "artifact_dir": str(args.artifact_dir),
        "source_platform": source_platform,
        "langfuse_trace_url": client.get_trace_url(trace_id=run_trace_id),
        "deleted_old_trace_ids": deleted_trace_ids,
        "already_missing_trace_ids": missing_trace_ids,
        "stitched_tasks": summaries,
        "agent_steps": total_agents,
        "tool_calls": total_tools,
        "root_direct_children": len(root_children),
        "note": "Original ATIF elapsed times/timestamps are metadata; Langfuse import-time duration is not task latency.",
    }, indent=2))


if __name__ == "__main__":
    main()
