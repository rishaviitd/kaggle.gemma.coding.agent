#!/usr/bin/env python3
"""Build the static trace catalog used by the project trace viewer.

Every trace at ``<trace-root>/<directory>/<split>/<task>/model_trace.json`` is
included. ``<directory>`` may have any depth, so a new collaborator or
experiment only needs to place its traces under ``logs/remote`` and rerun this
script.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from collections.abc import Mapping
from typing import Any


TRACE_NAME = "model_trace.json"
SPLITS = ("train", "dev", "val")


def read_resolved(trace_path: Path) -> bool | None:
    """Return the evaluation outcome when the trace records one."""
    try:
        payload: Any = json.loads(trace_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(payload, Mapping):
        return None
    final = payload.get("final")
    if not isinstance(final, Mapping):
        return None
    result = final.get("result")
    if not isinstance(result, Mapping):
        return None
    resolved = result.get("resolved")
    return resolved if isinstance(resolved, bool) else None


def build_index(project_root: Path, trace_root: Path) -> dict[str, Any]:
    """Discover traces below *trace_root*, grouped by their parent directory."""
    groups: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: {split: [] for split in SPLITS}
    )

    for trace_path in trace_root.rglob(TRACE_NAME):
        relative = trace_path.relative_to(trace_root)
        if len(relative.parts) < 3:
            continue
        *directory, split, task_id, filename = relative.parts
        if split not in SPLITS or filename != TRACE_NAME:
            continue

        entry: dict[str, Any] = {
            "task_id": task_id,
            "trace_path": trace_path.relative_to(project_root).as_posix(),
        }
        resolved = read_resolved(trace_path)
        if resolved is not None:
            entry["resolved"] = resolved
        groups["/".join(directory)][split].append(entry)

    iterations: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for directory in sorted(groups):
        iterations[directory] = {
            split: sorted(groups[directory][split], key=lambda entry: entry["task_id"])
            for split in SPLITS
        }
    return {"iterations": iterations}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--trace-root",
        type=Path,
        default=Path("logs/remote"),
        help="Directory to scan for model_trace.json files (default: logs/remote).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("trace-index.json"),
        help="Catalog JSON to write (default: trace-index.json).",
    )
    args = parser.parse_args()

    project_root = Path.cwd().resolve()
    trace_root = args.trace_root.resolve()
    output = args.output.resolve()
    if not trace_root.is_dir():
        parser.error(f"trace root does not exist: {trace_root}")
    try:
        trace_root.relative_to(project_root)
    except ValueError:
        parser.error("trace root must be inside the project root")

    index = build_index(project_root, trace_root)
    output.write_text(json.dumps(index, indent=2) + "\n")
    trace_count = sum(
        len(tasks)
        for directory in index["iterations"].values()
        for tasks in directory.values()
    )
    print(f"Wrote {output.relative_to(project_root)} with {trace_count} traces.")


if __name__ == "__main__":
    main()
