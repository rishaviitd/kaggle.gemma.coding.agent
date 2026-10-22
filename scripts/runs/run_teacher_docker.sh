#!/usr/bin/env bash
# Run the local Haiku continuation (run B) for one snapshot inside the oec-teacher image.
#
# Usage: run_teacher_docker.sh <snapshot_dir> <teacher_run_prefix> <task_id> [extra run_oec args...]
#
#   snapshot_dir         run A snapshot directory (contains handoff.json, manifest.json, ...).
#                        Mounted read-only at /snapshot.
#   teacher_run_prefix   run prefix for run B, e.g. oec-b1. It becomes <prefix>-a1 in logs/.
#                        MUST differ from the student run's name. task_pipeline deletes its own
#                        task log directory at start, so reusing the student's name would destroy
#                        the snapshot's source logs. The script refuses a prefix equal to the
#                        student run name it infers from the snapshot path
#                        (<...>/<student_run>/<split>/<task_id>/snapshot).
#   task_id              one verified task id, matching the snapshot's handoff.json.
#   extra run_oec args   passed through. Do not override --sandbox, --attempts, --workers or --stage.
#
# The repo is mounted read-write at /repo, so run logs land in the local checkout. The Anthropic key
# is passed by name only (-e ANTHROPIC_API_KEY); its value never appears on the command line.
# Use --platform linux/amd64 to match the Kaggle x86_64 environment. Emulation on arm64 is slow.
set -euo pipefail

usage='Usage: run_teacher_docker.sh <snapshot_dir> <teacher_run_prefix> <task_id> [extra run_oec args...]'
if [[ $# -lt 3 ]]; then
    echo "$usage" >&2
    exit 2
fi
if [[ ! -d "$1" ]]; then
    echo "snapshot_dir is not a directory: $1" >&2
    exit 2
fi
if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
    echo "ANTHROPIC_API_KEY is not set in this shell; export it before running (the value is not printed)." >&2
    exit 2
fi
if ! command -v docker >/dev/null 2>&1; then
    echo "docker is not on PATH" >&2
    exit 2
fi

snapshot="$(cd "$1" && pwd)"
run_prefix="$2"
task_id="$3"
shift 3

if [[ ! "$run_prefix" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
    echo "teacher_run_prefix may contain only letters, numbers, dots, underscores and hyphens" >&2
    exit 2
fi
for name in handoff.json manifest.json; do
    if [[ ! -f "$snapshot/$name" ]]; then
        echo "snapshot is missing $name: $snapshot" >&2
        exit 2
    fi
done
if ! grep -F -q "\"task_id\": \"$task_id\"" "$snapshot/handoff.json"; then
    echo "handoff.json task_id does not match $task_id" >&2
    exit 2
fi

# Infer the student run name from <...>/<student_run>/<split>/<task_id>/snapshot.
# run_oec names attempts <prefix>-a<N>, so the student prefix is the run name without -aN.
task_dir="$(dirname "$snapshot")"
split_dir="$(dirname "$task_dir")"
run_dir="$(dirname "$split_dir")"
if [[ "$(basename "$snapshot")" == snapshot && "$(basename "$task_dir")" == "$task_id" \
      && "$(basename "$split_dir")" =~ ^(train|dev|val)$ ]]; then
    student_run="$(basename "$run_dir")"
    student_prefix="${student_run%-a[0-9]*}"
    if [[ "$run_prefix" == "$student_run" || "$run_prefix" == "$student_prefix" \
          || "$run_prefix-a1" == "$student_run" ]]; then
        echo "teacher_run_prefix '$run_prefix' equals the student run '$student_run'; pick a new prefix." >&2
        exit 2
    fi
else
    echo "warning: cannot infer the student run name from the snapshot path; the prefix check is skipped." >&2
fi

repo="$(cd "$(dirname "$0")/../.." && pwd)"

exec docker run --rm --platform linux/amd64 \
  -e ANTHROPIC_API_KEY \
  -v "$repo:/repo" \
  -v "$snapshot:/snapshot:ro" \
  -v oec-teacher-uv-cache:/root/.cache/uv \
  -w /repo \
  oec-teacher:dev \
  python scripts/runs/run_oec.py --stage teacher --run-prefix "$run_prefix" \
    --task-ids "$task_id" --sandbox subprocess --attempts 1 --workers 1 \
    --handoff-snapshot /snapshot --api-base http://127.0.0.1:1/v1 "$@"
