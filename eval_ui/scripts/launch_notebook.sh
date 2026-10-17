#!/usr/bin/env bash
set -eu
project_root="$(cd -- "$(dirname -- "$0")/.." && pwd)"
export IPYTHONDIR="$project_root/.jupyter/ipython"
export JUPYTER_CONFIG_DIR="$project_root/.jupyter/config"
export JUPYTER_DATA_DIR="$project_root/.jupyter/data"
export JUPYTER_RUNTIME_DIR="$project_root/.jupyter/runtime"
cd "$project_root"
exec "$project_root/.venv/bin/python" -m jupyterlab notebooks/01_agent_trace_report.ipynb "$@"
