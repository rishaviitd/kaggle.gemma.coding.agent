#!/usr/bin/env python3
"""Create reproducible train, dev, and val splits with a shared distribution."""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Hashable


ROOT = Path(__file__).resolve().parents[1]
COLLABORATORS = ("akshay", "rishav", "sanyam")
SPLITS = ("train", "dev", "val")
TARGETS = {"train": 30, "dev": 40, "val": 59}
FACTORS = ("task_category", "difficulty", "file_band", "line_band")


def patch_stats(patch: str) -> tuple[int, int, int]:
    """Return changed-file count, additions, and deletions from a patch."""
    files: set[str] = set()
    additions = deletions = 0
    for line in patch.splitlines():
        if line.startswith("--- ") and not line.startswith("--- /dev/null"):
            path = line[4:].strip()
            files.add(path[2:] if path.startswith("a/") else path)
        elif line.startswith("+") and not line.startswith("+++ "):
            additions += 1
        elif line.startswith("-") and not line.startswith("--- "):
            deletions += 1
    return len(files), additions, deletions


def file_band(files: int) -> str:
    if files <= 1:
        return "1"
    if files == 2:
        return "2"
    return "3+"


def line_band(lines: int) -> str:
    if lines <= 10:
        return "1-10"
    if lines <= 30:
        return "11-30"
    if lines <= 80:
        return "31-80"
    return "81+"


def result_task_id(path: Path, result: dict[str, Any]) -> str:
    return str(result.get("task_id") or path.parent.parent.name)


def read_outcomes(log_roots: list[Path]) -> tuple[dict[str, bool], Counter[str]]:
    """Return base resolution and successful iteration-run counts by task.

    The same run stored under more than one log root is counted once.
    """
    base_resolved: dict[str, bool] = {}
    seen_iterations: set[tuple[str, str]] = set()
    iteration_successes: Counter[str] = Counter()
    for log_root in log_roots:
        if not log_root.is_dir():
            continue
        for split in ("train", "dev", "val"):
            for path in (log_root / "base" / split).glob("*/results/*.json"):
                result = json.loads(path.read_text())
                task_id = result_task_id(path, result)
                base_resolved[task_id] = base_resolved.get(task_id, False) or bool(result.get("resolved"))
        for collaborator in COLLABORATORS:
            for path in (log_root / collaborator).glob("itr-*/**/results/*.json"):
                result = json.loads(path.read_text())
                task_id = result_task_id(path, result)
                relative = path.relative_to(log_root).as_posix()
                key = (task_id, relative)
                if key in seen_iterations:
                    continue
                seen_iterations.add(key)
                if result.get("resolved"):
                    iteration_successes[task_id] += 1
    return base_resolved, iteration_successes


# Integer targets that reproduce the 129-task mix at 30 / 40 / 59.
MARGINAL_TARGETS = {
    "train": {
        "task_category": {"fastapi": 16, "rich": 11, "requests": 3, "httpx": 0},
        "difficulty": {"easy": 12, "hard": 18},
        "file_band": {"1": 21, "2": 4, "3+": 5},
        "line_band": {"1-10": 14, "11-30": 7, "31-80": 5, "81+": 4},
    },
    "dev": {
        "task_category": {"fastapi": 21, "rich": 15, "requests": 4, "httpx": 0},
        "difficulty": {"easy": 15, "hard": 25},
        "file_band": {"1": 28, "2": 6, "3+": 6},
        "line_band": {"1-10": 19, "11-30": 9, "31-80": 6, "81+": 6},
    },
    "val": {
        "task_category": {"fastapi": 30, "rich": 22, "requests": 6, "httpx": 1},
        "difficulty": {"easy": 23, "hard": 36},
        "file_band": {"1": 42, "2": 9, "3+": 8},
        "line_band": {"1-10": 27, "11-30": 13, "31-80": 9, "81+": 10},
    },
}


def assign_splits(rows: list[dict[str, Any]]) -> None:
    """Place every task so the three splits keep the same factor mix.

    Repository quotas are exact. Difficulty, file count, and patch lines are
    matched as closely as those exact repository counts allow.
    """
    counts: dict[str, dict[str, Counter[Hashable]]] = {
        split: {factor: Counter() for factor in FACTORS} for split in SPLITS
    }
    remaining = dict(TARGETS)

    def rarity(row: dict[str, Any]) -> tuple[int, int, str]:
        stratum = sum(1 for other in rows if all(other[factor] == row[factor] for factor in FACTORS))
        return (stratum, -int(row["patch_lines"]), row["task_id"])

    for row in sorted(rows, key=rarity):
        feasible = [
            split for split in SPLITS
            if remaining[split] > 0
            and counts[split]["task_category"][row["task_category"]]
            < MARGINAL_TARGETS[split]["task_category"][row["task_category"]]
        ]
        if not feasible:
            raise AssertionError(f"No repository capacity left for {row['task_id']}")
        best_split = min(feasible, key=lambda split: sum(
            (counts[split][factor][row[factor]] + 1 - MARGINAL_TARGETS[split][factor].get(row[factor], 0)) ** 2
            - (counts[split][factor][row[factor]] - MARGINAL_TARGETS[split][factor].get(row[factor], 0)) ** 2
            for factor in FACTORS if factor != "task_category"
        ))
        row["split"] = best_split
        remaining[best_split] -= 1
        for factor in FACTORS:
            counts[best_split][factor][row[factor]] += 1

    def error() -> int:
        total = 0
        for split in SPLITS:
            for factor in FACTORS:
                if factor == "task_category":
                    continue
                for group, target in MARGINAL_TARGETS[split][factor].items():
                    total += (counts[split][factor][group] - target) ** 2
        return total

    improved = True
    while improved:
        improved = False
        for left in rows:
            for right in rows:
                if left["task_id"] >= right["task_id"]:
                    continue
                if left["split"] == right["split"] or left["task_category"] != right["task_category"]:
                    continue
                before = error()
                a, b = left["split"], right["split"]
                for factor in FACTORS:
                    counts[a][factor][left[factor]] -= 1
                    counts[b][factor][right[factor]] -= 1
                    counts[b][factor][left[factor]] += 1
                    counts[a][factor][right[factor]] += 1
                left["split"], right["split"] = b, a
                if error() < before:
                    improved = True
                    continue
                left["split"], right["split"] = a, b
                for factor in FACTORS:
                    counts[b][factor][left[factor]] -= 1
                    counts[a][factor][right[factor]] -= 1
                    counts[a][factor][left[factor]] += 1
                    counts[b][factor][right[factor]] += 1


def build_rows(tasks_path: Path, log_roots: list[Path]) -> list[dict[str, Any]]:
    base_resolved, iteration_successes = read_outcomes(log_roots)
    rows: list[dict[str, Any]] = []
    for line in tasks_path.read_text().splitlines():
        if not line.strip():
            continue
        task = json.loads(line)
        task_id = task["instance_id"]
        patch_files, additions, deletions = patch_stats(task.get("patch", ""))
        patch_lines = additions + deletions
        base_success = base_resolved.get(task_id, False)
        iteration_success = iteration_successes[task_id]
        successful_runs = iteration_success + int(base_success)
        if successful_runs == 0:
            outcome_band = "never-solved"
        elif successful_runs == 1:
            outcome_band = "solved-once"
        elif successful_runs == 2:
            outcome_band = "solved-twice"
        else:
            outcome_band = "solved-three-plus"
        rows.append({
            "task_id": task_id,
            "repo": task["repo"],
            "task_category": task["repo"].rsplit("/", 1)[-1],
            "patch_files": patch_files,
            "patch_additions": additions,
            "patch_deletions": deletions,
            "patch_lines": patch_lines,
            "file_band": file_band(patch_files),
            "line_band": line_band(patch_lines),
            "patch_size_band": line_band(patch_lines),
            "base_resolved": base_success,
            "iteration_successes": iteration_success,
            "successful_runs": successful_runs,
            "outcome_band": outcome_band,
            "difficulty": "easy" if successful_runs else "hard",
            "multi_big_patch": patch_files > 1 or patch_lines > 100,
        })
    return rows


def validate(rows: list[dict[str, Any]]) -> None:
    by_split = Counter(row["split"] for row in rows)
    if dict(by_split) != TARGETS:
        raise AssertionError(f"Split counts mismatch: {dict(by_split)} != {TARGETS}")
    if len({row["task_id"] for row in rows}) != len(rows):
        raise AssertionError("A task appears more than once")


def report(rows: list[dict[str, Any]]) -> None:
    print("Split counts:", dict(sorted(Counter(row["split"] for row in rows).items())))
    for split in SPLITS:
        subset = [row for row in rows if row["split"] == split]
        print(
            f"{split}: repo={dict(sorted(Counter(row['task_category'] for row in subset).items()))} "
            f"difficulty={dict(sorted(Counter(row['difficulty'] for row in subset).items()))} "
            f"files={dict(sorted(Counter(row['file_band'] for row in subset).items()))} "
            f"lines={dict(sorted(Counter(row['line_band'] for row in subset).items()))}"
        )


def materialize_splits(tasks_path: Path, rows: list[dict[str, Any]]) -> None:
    """Rewrite split metadata and point symlinks at the shared asset files.

    Asset files under data/assets stay where they are. A symlink is removed
    only when it no longer belongs in that split folder.
    """
    tasks = {
        task["instance_id"]: task
        for task in (json.loads(line) for line in tasks_path.read_text().splitlines() if line.strip())
    }
    data_root = ROOT / "data"
    desired_links: dict[tuple[str, str], set[str]] = defaultdict(set)

    for split in SPLITS:
        split_rows = [row for row in rows if row["split"] == split]
        split_dir = data_root / split
        split_dir.mkdir(exist_ok=True)
        for subdir in ("snapshots", "graph", "embeddings"):
            (split_dir / subdir).mkdir(exist_ok=True)

        manifest_rows: list[dict[str, Any]] = []
        with (split_dir / "tasks.jsonl").open("w", encoding="utf-8") as output:
            for row in sorted(split_rows, key=lambda item: item["task_id"]):
                task = tasks[row["task_id"]]
                task_id = row["task_id"]
                repo = row["task_category"]
                base_commit = task["base_commit"]
                assets = {
                    "task_file": f"data/tasks.jsonl#{task_id}",
                    "snapshot_file": f"data/assets/snapshots/{task_id}.tgz",
                    "graph_file": f"data/assets/graph/{repo}_{base_commit}.json",
                    "embedding_file": f"data/assets/embeddings/{repo}_{base_commit}.npz",
                    "wheels_dir": f"data/assets/task_wheels/{task_id}",
                }
                manifest_rows.append({**row, "base_commit": base_commit, **assets})
                output.write(json.dumps(task, ensure_ascii=False) + "\n")
                for subdir, field in (
                    ("snapshots", "snapshot_file"),
                    ("graph", "graph_file"),
                    ("embeddings", "embedding_file"),
                ):
                    source = ROOT / assets[field]
                    if not source.exists():
                        raise FileNotFoundError(source)
                    desired_links[(split, subdir)].add(source.name)
                    link = split_dir / subdir / source.name
                    if link.is_symlink() and link.resolve() != source.resolve():
                        link.unlink()
                    if not link.exists():
                        link.symlink_to(Path(os.path.relpath(source, link.parent)))

        with (split_dir / "manifest.csv").open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=list(manifest_rows[0]))
            writer.writeheader()
            writer.writerows(manifest_rows)

    for split in SPLITS:
        for subdir in ("snapshots", "graph", "embeddings"):
            directory = data_root / split / subdir
            for link in directory.iterdir():
                if link.is_symlink() and link.name not in desired_links[(split, subdir)]:
                    link.unlink()

    outlier = data_root / "outlier"
    if outlier.exists():
        shutil.rmtree(outlier)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, default=ROOT / "data/tasks.jsonl")
    parser.add_argument("--logs", type=Path, nargs="+", default=[ROOT / "logs/remote", ROOT / "logs/archive/remote-1"])
    parser.add_argument("--output", type=Path, default=ROOT / "data/observed_task_split_map.csv")
    parser.add_argument("--materialize", action="store_true", help="Write split files and regroup symlinks")
    args = parser.parse_args()

    rows = build_rows(args.tasks, args.logs)
    if len(rows) != 129:
        raise AssertionError(f"Expected 129 tasks, found {len(rows)}")

    assign_splits(rows)
    validate(rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "split", "task_id", "repo", "task_category", "difficulty", "outcome_band", "base_resolved",
        "iteration_successes", "successful_runs", "multi_big_patch", "patch_files", "file_band",
        "patch_additions", "patch_deletions", "patch_lines", "line_band", "patch_size_band",
    ]
    with args.output.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda row: (row["split"], row["task_id"])))
    if args.materialize:
        materialize_splits(args.tasks, rows)
    report(rows)
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
