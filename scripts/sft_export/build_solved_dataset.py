#!/usr/bin/env python3
"""Build prefix and trajectory JSONL from every solved Haiku trace.

Train, val, and dev are all included. This tree has no separate test split; dev
is the third set. Every solved attempt is kept, including repeated solutions of
the same task in trajectory mode. Prefix rows are capped per task so a task that
was solved many times cannot dominate. Long tool observations are shortened in the
middle, and failed or repeated steps stay in the history but are not supervised.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import export_sft

ROOT = Path(__file__).resolve().parents[2]
TRACE_ROOT = ROOT / "logs/remote/rishav/inspiration-one"
POLICIES = ("none", "causal", "hindsight")
NO_CLIP = 10**9  # policy "none": keep every observation exactly as the harness returned it
OUTPUT = ROOT / "data/transient/sft-haiku-solved-v5"  # suffixed with the observation policy in main()
WHEELS_COPY = ROOT / "training/wheels/archive/sft"
MAX_MESSAGES = 512
MAX_CHARS = 3000
PREFIX_CAP_PER_TASK = 24
SPLITS = ("train", "val", "dev")


def solved_train_traces(root: Path) -> list[tuple[str, Path]]:
    """Every solved train, val, and dev trace. Repeated tasks stay in the data."""
    chosen: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    for results in sorted(root.glob("**/results/*.json")):
        if not any(part in SPLITS for part in results.parts):
            continue
        try:
            payload = json.loads(results.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("resolved") is not True:
            continue
        task_dir = results.parent.parent
        exchanges = task_dir / "anthropic_exchanges.json"
        if not exchanges.exists() or exchanges in seen:
            continue
        seen.add(exchanges)
        task_id = str(payload.get("task_id") or task_dir.name)
        chosen.append((task_id, exchanges))
    return chosen


def pick_evenly(count: int, quota: int) -> list[int]:
    """Indexes spread across 0..count-1 that always include the last step."""
    if quota >= count:
        return list(range(count))
    if quota == 1:
        return [count - 1]
    return sorted({round(i * (count - 1) / (quota - 1)) for i in range(quota)})


def cap_prefix_rows(per_trace: list[tuple[str, Path, list[dict]]], cap: int) -> list[tuple[str, Path, list[dict]]]:
    """Limit prefix rows per task. Every trace of a task keeps a share of the cap."""
    by_task: dict[str, list[int]] = {}
    for position, (task_id, _path, _rows) in enumerate(per_trace):
        by_task.setdefault(task_id, []).append(position)
    capped = list(per_trace)
    for positions in by_task.values():
        total = sum(len(per_trace[p][2]) for p in positions)
        if total <= cap:
            continue
        quota = max(1, cap // len(positions))
        for p in positions:
            task_id, path, rows = per_trace[p]
            capped[p] = (task_id, path, [rows[i] for i in pick_evenly(len(rows), quota)])
    return capped


def write_mode(mode: str, traces: list[tuple[str, Path]], output: Path, policy: str) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    rows_path = output / "train.jsonl"
    audit = []
    rejected = []
    per_trace = []
    for task_id, path in traces:
        try:
            raw = path.read_bytes()
            segments = export_sft.convert_task(json.loads(raw), retain_reasoning=False)
            examples = []
            for segment, row in enumerate(segments, 1):
                examples.extend(export_sft.build_examples(
                    row, mode=mode, task_id=task_id, segment=segment,
                    enable_thinking=True, max_messages=MAX_MESSAGES, max_chars=NO_CLIP if policy == "none" else MAX_CHARS,
                    observation_policy=policy))
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            rejected.append({"task_id": task_id, "source": str(path), "error": str(error)})
            continue
        per_trace.append((task_id, path, examples))
    before = sum(len(rows) for _t, _p, rows in per_trace)
    if mode == "prefix":
        per_trace = cap_prefix_rows(per_trace, PREFIX_CAP_PER_TASK)
    with rows_path.open("w") as handle:
        for task_id, path, examples in per_trace:
            for example in examples:
                example["source"] = str(path)
                handle.write(json.dumps(example, ensure_ascii=False) + "\n")
                audit.append({"task_id": task_id, "segment": example["segment"],
                              "messages": len(example["messages"]), "source": str(path)})
    manifest = {
        "schema_version": "gemma4-sft-v5",
        "mode": mode,
        "split": "train+val+dev",
        "filter": "haiku resolved == true, every solved attempt, train val and dev",
        "examples": len(audit),
        "examples_before_cap": before,
        "prefix_cap_per_task": PREFIX_CAP_PER_TASK if mode == "prefix" else None,
        "traces": len({row["source"] for row in audit}),
        "tasks": len({row["task_id"] for row in audit}),
        "max_messages": MAX_MESSAGES,
        "max_chars": NO_CLIP if policy == "none" else MAX_CHARS,
        "observation_policy": policy,
        "enable_thinking": True,
        "retain_reasoning": False,
        "rejected": rejected,
        "rows": audit,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main() -> None:
    policy = sys.argv[1] if len(sys.argv) > 1 else "causal"
    if policy not in POLICIES:
        raise SystemExit(f"policy must be one of {POLICIES}")
    output = OUTPUT.parent / f"{OUTPUT.name}-{policy}"
    traces = solved_train_traces(TRACE_ROOT)
    if not traces:
        raise SystemExit(f"No solved traces under {TRACE_ROOT}")
    if output.exists():
        raise SystemExit(f"Output already exists: {output}")
    print(f"solved tasks: {len(traces)}  observation policy: {policy}")
    for mode in ("trajectory",):
        manifest = write_mode(mode, traces, output / mode, policy)
        print(f"{mode}: {manifest['examples']} rows from {manifest['traces']} traces "
              f"({manifest['tasks']} tasks), rejected {len(manifest['rejected'])}")
        for item in manifest["rejected"]:
            print(f"  reject {item['task_id']}: {item['error']}")


if __name__ == "__main__":
    main()
