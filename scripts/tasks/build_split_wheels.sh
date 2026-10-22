#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."

# Optional: TASK_IDS_FILE=<file> limits the run to the task ids listed in the file
# (one per line). The file must live inside the repo, since the repo is mounted at
# /workspace in the container. Pass --list-only to print the selected task ids and
# exit before docker runs.
list_only=0
for arg in "$@"; do
  if [[ "$arg" == "--list-only" ]]; then
    list_only=1
  fi
done

# Manifest column 1 is task_id. Column 2 is repo. The unset path keeps its
# original column so its behaviour does not change.
if [[ -n "${TASK_IDS_FILE:-}" ]]; then
  id_column=1
else
  id_column=2
fi

if (( list_only )) && [[ -z "${TASK_IDS_FILE:-}" ]]; then
  echo "--list-only requires TASK_IDS_FILE to be set." >&2
  exit 1
fi

docker_env=()
if [[ -n "${TASK_IDS_FILE:-}" ]]; then
  case "$TASK_IDS_FILE" in
    /*) requested="$TASK_IDS_FILE" ;;
    *) requested="$PWD/$TASK_IDS_FILE" ;;
  esac
  if [[ ! -f "$requested" ]]; then
    echo "TASK_IDS_FILE does not exist: $TASK_IDS_FILE" >&2
    exit 1
  fi
  repo_root="$(pwd -P)"
  requested="$(cd "$(dirname "$requested")" && pwd -P)/$(basename "$requested")"
  case "$requested" in
    "$repo_root"/*) ;;
    *)
      echo "TASK_IDS_FILE must be inside the repo ($repo_root): $TASK_IDS_FILE" >&2
      exit 1
      ;;
  esac
  task_ids_in_container="/workspace/${requested#"$repo_root"/}"
  selected_ids="$(sed -e 's/[[:space:]]*$//' -e '/^$/d' "$requested")"
  if [[ -z "$selected_ids" ]]; then
    echo "TASK_IDS_FILE lists no task ids: $TASK_IDS_FILE" >&2
    exit 1
  fi

  known_ids=""
  for split in train dev val; do
    manifest="data/$split/manifest.csv"
    if [[ ! -f "$manifest" ]]; then
      echo "Missing $manifest. Run scripts/tasks/plan_observed_task_splits.py --materialize first." >&2
      exit 1
    fi
    known_ids+="$(awk -F, -v col="$id_column" 'NR > 1 { print $col }' "$manifest")"$'\n'
  done
  while IFS= read -r task_id; do
    if ! grep -qxF -- "$task_id" <<<"$known_ids"; then
      echo "TASK_IDS_FILE lists task id not found in any manifest: $task_id" >&2
      exit 1
    fi
  done <<<"$selected_ids"

  docker_env=(-e "TASK_IDS_FILE_IN_CONTAINER=$task_ids_in_container")
fi

shopt -s nullglob
for split in train dev val; do
  manifest="data/$split/manifest.csv"
  if [[ ! -f "$manifest" ]]; then
    echo "Missing $manifest. Run scripts/tasks/plan_observed_task_splits.py --materialize first." >&2
    exit 1
  fi
  while IFS= read -r task_id; do
    if [[ -n "${TASK_IDS_FILE:-}" ]] && ! grep -qxF -- "$task_id" <<<"$selected_ids"; then
      continue
    fi
    [[ -f "data/assets/snapshots/$task_id.tgz" ]] || {
      echo "Missing snapshot data/assets/snapshots/$task_id.tgz; restore the Kaggle bundle first." >&2
      exit 1
    }
  done < <(awk -F, -v col="$id_column" 'NR > 1 { print $col }' "$manifest")
done

if (( list_only )); then
  for split in train dev val; do
    manifest="data/$split/manifest.csv"
    while IFS= read -r task_id; do
      if grep -qxF -- "$task_id" <<<"$selected_ids"; then
        echo "$task_id"
      fi
    done < <(awk -F, -v col="$id_column" 'NR > 1 { print $col }' "$manifest")
  done
  exit 0
fi

docker run --platform linux/amd64 --rm -i \
  --user "$(id -u):$(id -g)" \
  -v "$PWD:/workspace" -w /workspace \
  ${docker_env[@]+"${docker_env[@]}"} \
  python:3.13-slim bash -s <<'CONTAINER_SCRIPT'
set -euo pipefail
shopt -s nullglob
export PIP_CACHE_DIR=/tmp/pip-cache UV_CACHE_DIR=/tmp/uv-cache
python -m pip install --target /tmp/task-build-tools -q uv
export PATH="/tmp/task-build-tools/bin:$PATH" PYTHONPATH=/tmp/task-build-tools
if [[ -n "${TASK_IDS_FILE_IN_CONTAINER:-}" ]]; then
  id_column=1
  selected_ids="$(sed -e 's/[[:space:]]*$//' -e '/^$/d' "$TASK_IDS_FILE_IN_CONTAINER")"
else
  id_column=2
fi
for split in train dev val; do
  manifest="data/$split/manifest.csv"
  while IFS= read -r task_id; do
    if [[ -n "${TASK_IDS_FILE_IN_CONTAINER:-}" ]] && ! grep -qxF -- "$task_id" <<<"$selected_ids"; then
      continue
    fi
    snapshot="data/assets/snapshots/$task_id.tgz"
    wheels="data/assets/task_wheels/$task_id"
    if [[ ! -f "$snapshot" ]]; then
      echo "Missing snapshot: $snapshot. Restore the Kaggle bundle first." >&2
      exit 1
    fi
    echo "Building $split/$task_id"
    # A cache is derived data. Rebuild it from scratch so stale wheels cannot
    # override the freshly collected requirements during injection.
    rm -rf "$wheels"
    mkdir -p "$wheels"
    tmp="$(mktemp -d)"
    tar --exclude="./.git" -xzf "$snapshot" -C "$tmp"
    cd "$tmp"

    python /workspace/scripts/tasks/collect_task_dependencies.py "$tmp" \
      --output /tmp/organizer-task-requirements.txt
    python -m pip download --dest "/workspace/$wheels" \
      -r /tmp/organizer-task-requirements.txt

    case "$task_id" in
      fastapi_*)
        if [[ -f requirements-tests.txt ]]; then
          python -m pip download --dest "/workspace/$wheels" \
            -r requirements-tests.txt "python-multipart>=0.0.18"
        else
          uv export --locked --no-default-groups --group tests --extra standard --no-hashes \
            --python "$(command -v python)" --format requirements-txt \
            --output-file /tmp/task-requirements.txt
          python -m pip download --dest "/workspace/$wheels" -r /tmp/task-requirements.txt
        fi
        python -m pip download --dest "/workspace/$wheels" "ujson>=5.8" pytest-asyncio
        ;;
      requests_*)
        python -m pip download --dest "/workspace/$wheels" -r requirements-dev.txt
        # httpbin 0.10.x relies on Werkzeug's pre-3.0 URL handling.
        rm -f "/workspace/$wheels"/werkzeug-*.whl
        python -m pip download --dest "/workspace/$wheels" "Werkzeug==2.3.8"
        ;;
      httpx_*)    python -m pip download --dest "/workspace/$wheels" -r requirements.txt ;;
      rich_*)     python -m pip download --dest "/workspace/$wheels" . pytest pytest-cov attrs "typing-extensions<5" ;;
      *) echo "Unknown repository task: $task_id" >&2; exit 1 ;;
    esac

    for source_dist in "/workspace/$wheels"/*.tar.gz "/workspace/$wheels"/*.zip; do
      [[ -f "$source_dist" ]] || continue
      python -m pip wheel --no-deps --wheel-dir "/workspace/$wheels" "$source_dist"
    done

    cd /workspace
    rm -rf "$tmp"
  done < <(awk -F, -v col="$id_column" 'NR > 1 { print $col }' "$manifest")
done
CONTAINER_SCRIPT
