#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

shopt -s nullglob
for split in train dev val; do
  manifest="data/$split/manifest.csv"
  if [[ ! -f "$manifest" ]]; then
    echo "Missing $manifest. Run scripts/plan_task_splits.py --materialize first." >&2
    exit 1
  fi
  while IFS= read -r task_id; do
    [[ -f "data/assets/snapshots/$task_id.tgz" ]] || {
      echo "Missing snapshot data/assets/snapshots/$task_id.tgz; restore the Kaggle bundle first." >&2
      exit 1
    }
  done < <(awk -F, 'NR > 1 { print $2 }' "$manifest")
done

docker run --platform linux/amd64 --rm -i \
  --user "$(id -u):$(id -g)" \
  -v "$PWD:/workspace" -w /workspace \
  python:3.13-slim bash -s <<'CONTAINER_SCRIPT'
set -euo pipefail
shopt -s nullglob
export PIP_CACHE_DIR=/tmp/pip-cache UV_CACHE_DIR=/tmp/uv-cache
python -m pip install --target /tmp/task-build-tools -q uv
export PATH="/tmp/task-build-tools/bin:$PATH" PYTHONPATH=/tmp/task-build-tools
python scripts/repair_task_wheels.py

for split in train dev val; do
  manifest="data/$split/manifest.csv"
  while IFS= read -r task_id; do
    snapshot="data/assets/snapshots/$task_id.tgz"
    wheels="data/assets/task_wheels/$task_id"
    if [[ ! -f "$snapshot" ]]; then
      echo "Missing snapshot: $snapshot. Restore the Kaggle bundle first." >&2
      exit 1
    fi
    if [[ -d "$wheels" ]] && compgen -G "$wheels/*.whl" >/dev/null; then
      echo "Skipping $task_id (shared wheel cache already exists)"
      continue
    fi

    echo "Building $split/$task_id"
    mkdir -p "$wheels"
    tmp="$(mktemp -d)"
    tar --exclude="./.git" -xzf "$snapshot" -C "$tmp"
    cd "$tmp"

    case "$task_id" in
      fastapi_*)
        if [[ -f requirements-tests.txt ]]; then
          python -m pip download --dest "/workspace/$wheels" -r requirements-tests.txt
        else
          uv export --locked --no-default-groups --group tests --no-hashes \
            --python "$(command -v python)" --format requirements-txt \
            --output-file /tmp/task-requirements.txt
          python -m pip download --dest "/workspace/$wheels" -r /tmp/task-requirements.txt
        fi
        ;;
      requests_*) python -m pip download --dest "/workspace/$wheels" -r requirements-dev.txt ;;
      httpx_*)    python -m pip download --dest "/workspace/$wheels" -r requirements.txt ;;
      rich_*)     python -m pip download --dest "/workspace/$wheels" . pytest pytest-cov attrs "typing-extensions<5" ;;
      *) echo "Unknown repository task: $task_id" >&2; exit 1 ;;
    esac

    cd /workspace
    rm -rf "$tmp"
  done < <(awk -F, 'NR > 1 { print $2 }' "$manifest")
done
CONTAINER_SCRIPT
