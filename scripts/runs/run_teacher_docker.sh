#!/usr/bin/env bash
# Run the local Haiku continuation (run B) for one snapshot inside the oec-teacher image.
#
# Usage: run_teacher_docker.sh <snapshot_dir> <teacher_run_prefix> <task_id> [extra run_oec args...]
#
#   snapshot_dir         run A snapshot directory (contains handoff.json, manifest.json, ...).
#                        Mounted read-only at /snapshot. Must sit at
#                        <...>/<student_run>/<split>/<task_id>/snapshot (the layout run_oec writes).
#   teacher_run_prefix   run prefix for run B, e.g. oec-b1. It becomes <prefix>-a1 in logs/.
#                        MUST differ from the student run's name. task_pipeline deletes its own
#                        task log directory at start, so reusing the student's name would destroy
#                        the snapshot. The script refuses this, and it also refuses when the
#                        snapshot's real path lies inside the directory task_pipeline is about to delete.
#   task_id              one verified task id, matching the snapshot's handoff.json.
#   extra run_oec args   passed through. Do not pass --submission, --run-prefix, --task-ids,
#                        --stage, --handoff-snapshot, --sandbox, --attempts or --workers; they are
#                        refused, because they would change the paths checked below.
#
# Environment:
#   ANTHROPIC_API_KEY                   required. Passed by name only (-e ANTHROPIC_API_KEY). Its value
#                                       never appears on the command line or in this script's output.
#   ALLOW_UNRECOGNIZED_SNAPSHOT_PATH=1  opt-in override for a snapshot path that does not match
#                                       <student_run>/<split>/<task_id>/snapshot. The student-run
#                                       prefix check is then skipped; the deletion-target check still runs.
#
# The repo is mounted read-write at /repo, so run logs land in the local checkout. The container's
# /repo/.env is hidden with /dev/null so only the shell-provided key is visible. Container
# ownership of /repo/logs is restored to the host user on exit.
# Use --platform linux/amd64 to match the Kaggle x86_64 environment. Emulation on arm64 is slow.
set -euo pipefail

usage='Usage: run_teacher_docker.sh <snapshot_dir> <teacher_run_prefix> <task_id> [extra run_oec args...]'
# Must match run_oec.py's --submission default (src/rishav/inspiration-one) relative to src/.
SUBMISSION_REL='rishav/inspiration-one'

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
if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is required on the host to read handoff.json" >&2
    exit 2
fi

snapshot="$(cd "$1" && pwd -P)"
run_prefix="$2"
task_id="$3"
shift 3

for arg in "$@"; do
    case "$arg" in
        --submission|--submission=*|--run-prefix|--run-prefix=*|--task-ids|--task-ids=*|\
--stage|--stage=*|--handoff-snapshot|--handoff-snapshot=*|--sandbox|--sandbox=*|\
--attempts|--attempts=*|--workers|--workers=*)
            echo "extra argument $arg is not allowed; the wrapper fixes it" >&2
            exit 2 ;;
    esac
done

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
# Parse the JSON rather than grepping it, so spacing and key order do not matter.
if ! python3 -I -c 'import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    handoff = json.load(f)
sys.exit(0 if isinstance(handoff, dict) and handoff.get("task_id") == sys.argv[2] else 1)' \
        "$snapshot/handoff.json" "$task_id"; then
    echo "handoff.json task_id does not match $task_id" >&2
    exit 2
fi

repo="$(cd "$(dirname "$0")/../.." && pwd -P)"

# Infer the student run from <...>/<student_run>/<split>/<task_id>/snapshot.
student_run=""
if [[ "$(basename "$snapshot")" == snapshot && "$(basename "$(dirname "$snapshot")")" == "$task_id" \
      && "$(basename "$(dirname "$(dirname "$snapshot")")")" =~ ^(train|dev|val)$ ]]; then
    student_run="$(basename "$(dirname "$(dirname "$(dirname "$snapshot")")")")"
fi
if [[ -z "$student_run" ]]; then
    if [[ "${ALLOW_UNRECOGNIZED_SNAPSHOT_PATH:-}" != 1 ]]; then
        echo "snapshot path does not match <student_run>/<split>/$task_id/snapshot: $snapshot" >&2
        echo "refusing: the student run name cannot be checked. Set ALLOW_UNRECOGNIZED_SNAPSHOT_PATH=1 to override." >&2
        exit 2
    fi
    echo "warning: ALLOW_UNRECOGNIZED_SNAPSHOT_PATH=1; skipping the student-run prefix check" >&2
else
    # run_oec names attempts <prefix>-a<N>, so the student prefix is the run name without -aN.
    student_prefix="${student_run%-a[0-9]*}"
    if [[ "$run_prefix" == "$student_run" || "$run_prefix" == "$student_prefix" \
          || "$run_prefix-a1" == "$student_run" ]]; then
        echo "teacher_run_prefix '$run_prefix' equals the student run '$student_run'; pick a new prefix." >&2
        exit 2
    fi
fi

# Refuse if task_pipeline's start-up rmtree would delete the snapshot. It removes
# <repo>/logs/remote/<SUBMISSION_REL>/<prefix>-a1/<split>/<task_id> (run_oec.job_logs layout).
for split in train dev val; do
    target="$repo/logs/remote/$SUBMISSION_REL/$run_prefix-a1/$split/$task_id"
    if [[ -d "$target" ]]; then
        target_real="$(cd "$target" && pwd -P)"
        if [[ "$snapshot" == "$target_real" || "$snapshot" == "$target_real"/* ]]; then
            echo "refusing: $snapshot lies inside $target_real, which task_pipeline deletes at start." >&2
            exit 2
        fi
    fi
done

# Keep the passed key authoritative: /repo/.env is hidden only when the host has one.
env_mounts=()
if [[ -e "$repo/.env" ]]; then
    env_mounts=(-v /dev/null:/repo/.env:ro)
fi

# task_pipeline calls provider_key('vllm') unconditionally in run(), before the resume branch, and
# run_oec always passes --provider vllm. The placeholder only satisfies that check. On resume the
# student is replayed from the snapshot, preflight is skipped, and --api-base points at the dead
# local endpoint http://127.0.0.1:1/v1, so the value is never sent anywhere real. Not a secret.
# --init makes tini PID 1 so SIGTERM/SIGINT reach the entrypoint and its chown trap runs.
exec docker run --rm --init --platform linux/amd64 \
  -e ANTHROPIC_API_KEY \
  -e VLLM_API_KEY=unused \
  -e "HOST_UID=$(id -u)" -e "HOST_GID=$(id -g)" \
  -v "$repo:/repo" \
  ${env_mounts[@]+"${env_mounts[@]}"} \
  -v "$snapshot:/snapshot:ro" \
  -v oec-teacher-uv-cache:/root/.cache/uv \
  -w /repo \
  oec-teacher:dev \
  python scripts/runs/run_oec.py --stage teacher --run-prefix "$run_prefix" \
    --task-ids "$task_id" --sandbox subprocess --attempts 1 --workers 1 \
    --handoff-snapshot /snapshot --api-base http://127.0.0.1:1/v1 "$@"
