#!/usr/bin/env python3
"""Plan deterministic train/dev/val splits and materialize lightweight references."""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SPLITS = ("train", "dev", "val")
DEFAULT_TARGETS = {"train": 76, "dev": 27, "val": 27}


def patch_stats(patch: str) -> tuple[int, int, int]:
    """Return changed-file count, added lines, and removed lines from a diff."""
    files: set[str] = set()
    additions = deletions = 0
    for line in patch.splitlines():
        if line.startswith("+++ b/"):
            files.add(line[6:])
        elif line.startswith("--- a/"):
            files.add(line[6:])
        elif line.startswith("+") and not line.startswith("+++"):
            additions += 1
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1
    files.discard("/dev/null")
    return len(files), additions, deletions


def percentile_ranks(values: dict[str, int]) -> dict[str, float]:
    ordered = sorted(values, key=lambda key: (values[key], key))
    if len(ordered) < 2:
        return {key: 0.0 for key in ordered}
    return {key: index / (len(ordered) - 1) for index, key in enumerate(ordered)}


def assign_difficulty(rows: list[dict[str, Any]]) -> None:
    patch_volume = {r["instance_id"]: r["patch_lines"] for r in rows}
    issue_lengths = {r["instance_id"]: r["issue_chars"] for r in rows}
    patch_rank = percentile_ranks(patch_volume)
    issue_rank = percentile_ranks(issue_lengths)
    for row in rows:
        row["difficulty_score"] = (patch_rank[row["instance_id"]] + issue_rank[row["instance_id"]]) / 2
    ordered = sorted(rows, key=lambda r: (r["difficulty_score"], r["instance_id"]))
    count = len(ordered)
    for index, row in enumerate(ordered):
        band = min(2, index * 3 // count)
        row["difficulty"] = ("easy", "medium", "hard")[band]


def load_tasks(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    seen: set[str] = set()
    for row in rows:
        task_id = row["instance_id"]
        if task_id in seen:
            raise ValueError(f"Duplicate task ID: {task_id}")
        seen.add(task_id)
        files, added, deleted = patch_stats(row.get("patch", ""))
        row["repo_short"] = row["repo"].rstrip("/").rsplit("/", 1)[-1]
        row["issue_chars"] = len(row.get("problem_statement", "").strip())
        row["patch_files"] = files
        row["patch_additions"] = added
        row["patch_deletions"] = deleted
        row["patch_lines"] = added + deleted
    if not rows:
        raise ValueError(f"No tasks found in {path}")
    assign_difficulty(rows)
    return rows


def _imbalance(
    counts: dict[tuple[str, str], Counter[str]],
    totals: Counter[str],
    stratum_sizes: Counter[tuple[str, str]],
    targets: dict[str, int],
    total_items: int,
) -> float:
    score = 0.0
    for (repo, difficulty), size in stratum_sizes.items():
        observed = counts[(repo, difficulty)]
        for split in SPLITS:
            expected = size * targets[split] / total_items
            score += (observed[split] - expected) ** 2 / max(expected, 0.5)
    for split in SPLITS:
        score += 2 * (totals[split] - targets[split]) ** 2 / targets[split]
    return score


def assign_splits(
    rows: list[dict[str, Any]], targets: dict[str, int], seed: int
) -> dict[str, set[str]]:
    """Greedily balance repo × difficulty strata while keeping base commits together."""
    httpx = [r for r in rows if r["repo_short"] == "httpx"]
    if len(httpx) != 1:
        raise ValueError(f"Expected exactly one HTTPX task for the agreed overlap; found {len(httpx)}")
    overlap_id = httpx[0]["instance_id"]
    exclusive_rows = [r for r in rows if r["instance_id"] != overlap_id]
    exclusive_targets = {"train": targets["train"], "dev": targets["dev"] - 1, "val": targets["val"] - 1}
    if sum(exclusive_targets.values()) != len(exclusive_rows):
        raise ValueError(
            f"Targets imply {sum(exclusive_targets.values())} unique non-HTTPX tasks, "
            f"but found {len(exclusive_rows)}"
        )

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in exclusive_rows:
        grouped[(row["repo"], row["base_commit"])].append(row)
    groups = list(grouped.values())
    duplicates = [group for group in groups if len(group) > 1]
    singles = [group[0] for group in groups if len(group) == 1]
    if any(len(group) > min(exclusive_targets.values()) for group in groups):
        raise ValueError("A repo/base_commit group is larger than every target split")

    strata = Counter((r["repo_short"], r["difficulty"]) for r in exclusive_rows)
    rng = random.Random(seed)
    best: tuple[float, dict[str, set[str]]] | None = None
    # There are only two known duplicate groups, so evaluate all placements.
    placements: list[tuple[str, ...]] = [()]
    for _ in duplicates:
        placements = [prefix + (split,) for prefix in placements for split in SPLITS]

    for placement in placements:
        counts: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
        totals: Counter[str] = Counter()
        assignment = {split: set() for split in SPLITS}
        remaining = dict(exclusive_targets)
        valid = True
        for group, split in zip(duplicates, placement):
            if remaining[split] < len(group):
                valid = False
                break
            remaining[split] -= len(group)
            for row in group:
                assignment[split].add(row["instance_id"])
                counts[(row["repo_short"], row["difficulty"])][split] += 1
                totals[split] += 1
        if not valid:
            continue

        by_stratum: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in singles:
            by_stratum[(row["repo_short"], row["difficulty"])].append(row)
        stratum_keys = list(by_stratum)
        rng.shuffle(stratum_keys)
        for key in stratum_keys:
            rng.shuffle(by_stratum[key])
        ordered_singles = [row for key in stratum_keys for row in by_stratum[key]]

        for row in ordered_singles:
            key = (row["repo_short"], row["difficulty"])
            candidates = [split for split in SPLITS if remaining[split] > 0]
            if not candidates:
                valid = False
                break
            before = _imbalance(counts, totals, strata, exclusive_targets, len(exclusive_rows))
            costs = []
            for split in candidates:
                counts[key][split] += 1
                totals[split] += 1
                remaining[split] -= 1
                after = _imbalance(counts, totals, strata, exclusive_targets, len(exclusive_rows))
                counts[key][split] -= 1
                totals[split] -= 1
                remaining[split] += 1
                costs.append((after - before, rng.random(), split))
            _, _, chosen = min(costs)
            counts[key][chosen] += 1
            totals[chosen] += 1
            remaining[chosen] -= 1
            assignment[chosen].add(row["instance_id"])
        if not valid or any(remaining.values()):
            continue
        score = _imbalance(counts, totals, strata, exclusive_targets, len(exclusive_rows))
        if best is None or score < best[0]:
            best = (score, assignment)

    if best is None:
        raise RuntimeError("Could not allocate task groups to the requested split sizes")
    result = best[1]
    result["dev"].add(overlap_id)
    result["val"].add(overlap_id)
    return result


def map_rows(
    rows: list[dict[str, Any]], assignments: dict[str, set[str]], root: Path
) -> list[dict[str, Any]]:
    by_id = {r["instance_id"]: r for r in rows}
    output = []
    for split in SPLITS:
        for task_id in sorted(assignments[split]):
            row = by_id[task_id]
            repo = row["repo_short"]
            base = row["base_commit"]
            assets = {
                "task_file": "data/tasks.jsonl#" + task_id,
                "snapshot_file": f"data/assets/snapshots/{task_id}.tgz",
                "graph_file": f"data/assets/graph/{repo}_{base}.json",
                "embedding_file": f"data/assets/embeddings/{repo}_{base}.npz",
                "wheels_dir": f"data/assets/task_wheels/{task_id}"
            }
            for key, value in assets.items():
                if key == "wheels_dir":
                    continue  # Created locally by build_split_wheels.sh.
                asset_path = root / value.split("#", 1)[0]
                exists = asset_path.exists()
                if not exists:
                    raise FileNotFoundError(f"Missing {key} for {task_id}: {value}")
            output.append({
                "split": split,
                "task_id": task_id,
                "repo": row["repo"],
                "base_commit": base,
                "difficulty": row["difficulty"],
                "difficulty_score": f"{row['difficulty_score']:.4f}",
                "issue_chars": row["issue_chars"],
                "patch_files": row["patch_files"],
                "patch_additions": row["patch_additions"],
                "patch_deletions": row["patch_deletions"],
                "patch_lines": row["patch_lines"],
                **assets,
            })
    return output


def report(rows: list[dict[str, Any]], assignments: dict[str, set[str]]) -> None:
    by_id = {r["instance_id"]: r for r in rows}
    print("Split counts (HTTPX intentionally appears in dev and val):")
    for split in SPLITS:
        items = [by_id[task_id] for task_id in assignments[split]]
        repos = Counter(r["repo_short"] for r in items)
        levels = Counter(r["difficulty"] for r in items)
        issue_mid = median(r["issue_chars"] for r in items)
        patch_mid = median(r["patch_lines"] for r in items)
        print(
            f"  {split:5} {len(items):2}  repos={dict(sorted(repos.items()))}  "
            f"difficulty={dict(sorted(levels.items()))}  "
            f"median_issue_chars={issue_mid:g} median_patch_lines={patch_mid:g}"
        )
    print("Repo × difficulty by split:")
    strata = sorted({(r["repo_short"], r["difficulty"]) for r in rows})
    for repo, difficulty in strata:
        counts = [sum(
            task_id in assignments[split]
            and by_id[task_id]["repo_short"] == repo
            and by_id[task_id]["difficulty"] == difficulty
            for task_id in assignments[split]
        ) for split in SPLITS]
        print(f"  {repo:8} {difficulty:6} train={counts[0]:2} dev={counts[1]:2} val={counts[2]:2}")


def materialize_splits(
    tasks_path: Path, rows: list[dict[str, Any]], root: Path
) -> None:
    task_by_id = {
        task["instance_id"]: task
        for task in (json.loads(line) for line in tasks_path.read_text().splitlines() if line.strip())
    }
    assets_root = root / "data" / "assets"
    for subdir in ("snapshots", "graph", "embeddings", "task_wheels"):
        (assets_root / subdir).mkdir(parents=True, exist_ok=True)

    for split in SPLITS:
        split_dir = root / "data" / split
        split_dir.mkdir(parents=True, exist_ok=True)
        for subdir in ("snapshots", "graph", "embeddings"):
            (split_dir / subdir).mkdir(exist_ok=True)
        split_rows = [row for row in rows if row["split"] == split]
        with (split_dir / "tasks.jsonl").open("w", encoding="utf-8") as output:
            for row in split_rows:
                output.write(json.dumps(task_by_id[row["task_id"]], ensure_ascii=False) + "\n")
        with (split_dir / "manifest.csv").open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=list(split_rows[0]))
            writer.writeheader()
            writer.writerows(split_rows)

        desired = {
            "snapshots": {Path(row["snapshot_file"]).name for row in split_rows},
            "graph": {Path(row["graph_file"]).name for row in split_rows},
            "embeddings": {Path(row["embedding_file"]).name for row in split_rows},
        }
        for subdir, names in desired.items():
            for entry in (split_dir / subdir).iterdir():
                if entry.is_symlink() and entry.name not in names:
                    entry.unlink()

        for row in split_rows:
            (assets_root / "task_wheels" / row["task_id"]).mkdir(parents=True, exist_ok=True)
            targets = (
                (row["snapshot_file"], split_dir / "snapshots" / Path(row["snapshot_file"]).name),
                (row["graph_file"], split_dir / "graph" / Path(row["graph_file"]).name),
                (row["embedding_file"], split_dir / "embeddings" / Path(row["embedding_file"]).name),
            )
            for source_name, link in targets:
                source = root / source_name
                if link.is_symlink():
                    if link.resolve() != source.resolve():
                        link.unlink()
                elif link.exists():
                    raise FileExistsError(f"Expected a symlink, found a physical split asset: {link}")
                if not link.is_symlink():
                    link.symlink_to(Path(os.path.relpath(source, link.parent)),
                                    target_is_directory=source.is_dir())

        # Remove legacy per-split wheel references; wheel caches are task-owned
        # and live once under data/assets/task_wheels regardless of split.
        legacy_wheels = split_dir / "wheels"
        if legacy_wheels.exists() and legacy_wheels.is_dir():
            for entry in legacy_wheels.iterdir():
                if entry.is_symlink() and not entry.exists():
                    entry.unlink()
                elif entry.is_dir() and any(entry.glob("*.whl")):
                    target = assets_root / "task_wheels" / entry.name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if not target.exists():
                        shutil.move(str(entry), str(target))
            if not any(legacy_wheels.iterdir()):
                legacy_wheels.rmdir()
        print(f"Materialized {split}: {len(split_rows)} task rows under {split_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, default=ROOT / "data/tasks.jsonl")
    parser.add_argument("--output", type=Path, default=ROOT / "data/task_split_map.csv")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--train-count", type=int, default=DEFAULT_TARGETS["train"])
    parser.add_argument("--dev-count", type=int, default=DEFAULT_TARGETS["dev"])
    parser.add_argument("--val-count", "--test-count", dest="val_count", type=int, default=DEFAULT_TARGETS["val"])
    parser.add_argument("--materialize", action="store_true", help="Create split metadata and symlinks to shared assets")
    args = parser.parse_args()
    targets = {"train": args.train_count, "dev": args.dev_count, "val": args.val_count}
    if any(targets[s] <= 0 for s in SPLITS):
        parser.error("split counts must be positive")

    tasks = load_tasks(args.tasks)
    assignments = assign_splits(tasks, targets, args.seed)
    root = args.tasks.resolve().parent.parent
    # Accept the organizer's plural folder name and older root-level layout,
    # then put all physical assets in one canonical location.
    assets_root = root / "data" / "assets"
    assets_root.mkdir(parents=True, exist_ok=True)
    for canonical, legacy_names in {
        "snapshots": ("snapshots",),
        "graph": ("graph", "graphs"),
        "embeddings": ("embeddings",),
    }.items():
        destination = assets_root / canonical
        destination.mkdir(exist_ok=True)
        for legacy_name in legacy_names:
            legacy = root / "data" / legacy_name
            if legacy == destination or not legacy.is_dir():
                continue
            for source in legacy.iterdir():
                target = destination / source.name
                if not target.exists() and not target.is_symlink():
                    shutil.move(str(source), str(target))
    rows = map_rows(tasks, assignments, root)
    expected = {"train": args.train_count, "dev": args.dev_count, "val": args.val_count}
    actual = {split: len(assignments[split]) for split in SPLITS}
    if actual != expected:
        raise AssertionError(f"Split count mismatch: expected {expected}, got {actual}")

    # Same repo/base commit must stay together, except the requested HTTPX overlap.
    grouped: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in tasks:
        grouped[(row["repo"], row["base_commit"])].add(row["instance_id"])
    for key, ids in grouped.items():
        memberships = {split for split in SPLITS if assignments[split].intersection(ids)}
        allowed = {"dev", "val"} if all(row["repo_short"] == "httpx" for row in tasks if row["instance_id"] in ids) else None
        if allowed is not None:
            if memberships != allowed:
                raise AssertionError(f"HTTPX task overlap is wrong: {memberships}")
        elif len(memberships) != 1:
            raise AssertionError(f"Repo/base_commit leaks across splits: {key} -> {memberships}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    columns = list(rows[0])
    with args.output.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    if args.materialize:
        materialize_splits(args.tasks, rows, root)
    report(tasks, assignments)
    print(f"Map written to {args.output}")
    print(f"Unique tasks: {len(tasks)}; split memberships: {sum(actual.values())}")


if __name__ == "__main__":
    main()
