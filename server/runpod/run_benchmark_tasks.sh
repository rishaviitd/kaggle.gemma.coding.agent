#!/usr/bin/env bash
# Eval + benchmark: 20 val tasks, itr-1 configs, RunPod E4B vLLM.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
export RUNPOD_API_BASE="${RUNPOD_API_BASE:-https://t4r30ysyfmt164-8000.proxy.runpod.net/v1}"
export EVAL_WORKERS="${EVAL_WORKERS:-8}"
export BENCHMARK_RUN_NAME="${BENCHMARK_RUN_NAME:-e4b-benchmark}"
exec "$ROOT/.venv/bin/python" -u "$ROOT/server/runpod/run_eval.py" "$@"
