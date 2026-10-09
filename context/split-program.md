# Three-way task split program

## Purpose

Assign all 129 tasks to exactly one of three folders, with the same mix in each:

- `train`: 30 tasks
- `dev`: 40 tasks
- `val`: 59 tasks

There is no outlier split and no overlapping task.

## Balance

Each split follows the full-set mix of:

1. repository: fastapi, rich, requests, httpx
2. gold-patch file count: 1, 2, or 3 or more
3. gold-patch lines: 1–10, 11–30, 31–80, 81 or more
4. difficulty: easy when base or any Akshay, Rishav, or Sanyam iteration resolved it, hard when none did

httpx has one task, so that task sits in val. The other factors match to the nearest task.

## Outputs

`scripts/plan_observed_task_splits.py` writes `data/observed_task_split_map.csv`.
With `--materialize` it rewrites `data/train`, `data/dev`, and `data/val`
manifests and `tasks.jsonl`, and points their snapshot, graph, and embedding
symlinks at `data/assets`. Asset files are not moved or deleted. A symlink is
removed only when that task no longer belongs in that split folder.

## Reproducibility

The assignment is deterministic. It reads:

- `data/tasks.jsonl` for the reference patch and repository
- `logs/remote/` and `logs/archive/remote-1/` for resolved outcomes

The same run stored in both log roots counts once. Running again with
unchanged inputs produces the same split.
