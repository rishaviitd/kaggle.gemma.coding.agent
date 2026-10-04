#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

docker run --platform linux/amd64 --rm \
  -v "$ROOT:/workspace" \
  swebench-sandbox:latest bash -eu -c '
    mkdir -p /tmp/task /workspace/data/assets/task_wheels/fastapi_11355
    tar --exclude="./.git" \
      -xzf /workspace/data/assets/snapshots/fastapi_11355.tgz \
      -C /tmp/task
    cd /tmp/task
    python -m pip wheel -r requirements-tests.txt \
      --wheel-dir /workspace/data/assets/task_wheels/fastapi_11355
  '

docker run --platform linux/amd64 --rm \
  -v "$ROOT:/workspace" \
  swebench-sandbox:latest bash -eu -c '
    python -m pip install --target /tmp/task-build-tools -q uv
    export PATH="/tmp/task-build-tools/bin:$PATH"
    export PYTHONPATH=/tmp/task-build-tools
    for task_id in fastapi_14616 fastapi_14953; do
      wheel_dir="/workspace/data/assets/task_wheels/$task_id"
      if compgen -G "$wheel_dir/*multipart*.whl" >/dev/null; then
        echo "Multipart wheel already exists for $task_id; skipping build"
        continue
      fi
      task_dir="$(mktemp -d)"
      mkdir -p "$wheel_dir"
      tar --exclude="./.git" \
        -xzf "/workspace/data/assets/snapshots/$task_id.tgz" \
        -C "$task_dir"
      (
        cd "$task_dir"
        if [[ -f requirements-tests.txt ]]; then
          python -m pip download --exists-action=w --dest "$wheel_dir" -r requirements-tests.txt
        else
          uv export --locked --no-default-groups --group tests --extra standard --no-hashes \
            --python "$(command -v python)" --format requirements-txt \
            --output-file /tmp/task-requirements.txt
          python -m pip download --exists-action=w --dest "$wheel_dir" -r /tmp/task-requirements.txt
        fi
      )
      rm -rf "$task_dir"
    done
  '

.venv/bin/python scripts/evaluate.py \
  --task-id fastapi_11355 --split train --reference-check

for task_id in fastapi_14616 fastapi_14953; do
  .venv/bin/python scripts/task_pipeline.py \
    --task-id "$task_id" --split train \
    --api-base http://127.0.0.1:18001/v1 \
    --skip-langfuse
done
