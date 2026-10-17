#!/usr/bin/env bash
# Two more runs of each prompt. The finished e4b-benchmark and
# e4b-inspiration-one runs are the first sample.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
export EVAL_WORKERS=8
OUT="$ROOT/logs/remote/rishav/pass3-e4b"
mkdir -p "$OUT"
for n in 2 3; do
  echo "START itr-1 run $n $(date -u +%H:%M:%S)" | tee -a "$OUT/launcher.log"
  BENCHMARK_SUBMISSION=src/rishav/itr-1 BENCHMARK_RUN_NAME="e4b-itr1-p3-r${n}" \
    bash llm_server/runpod/run_benchmark_tasks.sh
  echo "START inspiration-one run $n $(date -u +%H:%M:%S)" | tee -a "$OUT/launcher.log"
  BENCHMARK_SUBMISSION=src/rishav/inspiration-one BENCHMARK_RUN_NAME="e4b-insp-p3-r${n}" \
    bash llm_server/runpod/run_benchmark_tasks.sh
done
"$ROOT/.venv/bin/python" - << 'PY'
import json, statistics
from pathlib import Path
root = Path("logs/remote/rishav")
specs = {
    "itr-1": [root/"itr-1"/"e4b-benchmark"/"val", *(root/"itr-1"/f"e4b-itr1-p3-r{n}"/"val" for n in (2,3))],
    "inspiration-one": [root/"inspiration-one"/"e4b-inspiration-one"/"val", *(root/"inspiration-one"/f"e4b-insp-p3-r{n}"/"val" for n in (2,3))],
}
report = {}
for name, runs in specs.items():
    counts = []
    solved_sets = []
    for run in runs:
        ids = []
        for path in run.glob("*/results/*.json"):
            row = json.loads(path.read_text())
            if row.get("resolved") is True:
                ids.append(row["task_id"])
        counts.append(len(ids))
        solved_sets.append(set(ids))
    union = set.union(*solved_sets) if solved_sets else set()
    report[name] = {
        "solved_per_run": counts,
        "min": min(counts),
        "median": statistics.median(counts),
        "avg": round(sum(counts)/len(counts), 2),
        "max": max(counts),
        "pass_at_3_tasks": sorted(union),
        "pass_at_3": len(union),
    }
out = root/"pass3-e4b"/"summary.json"
out.write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report, indent=2))
PY
