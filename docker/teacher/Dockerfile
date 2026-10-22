# syntax=docker/dockerfile:1.4
# Teacher image for run B (local Haiku continuation) of the E1 OEC two-run split.
#
# Build (linux/amd64 to match the Kaggle x86_64 kernel). On an arm64 host this runs under
# emulation and is slow, mainly the uv Python and dependency installs:
#   docker build --platform linux/amd64 -t oec-teacher:dev docker/teacher
#
# Reproduced from data/transient/kaggle-oec-gen/gen_dry.py (install_py313_harness):
#   - System interpreter CPython 3.12. The Kaggle kernel image is
#     gcr.io/kaggle-private-byod/python@sha256:37c64f7dd9c54116ecd1bcc88817c5469b88387388fade02bfa8bf3fc647d461
#     (kernel-metadata.json). It is private and was not pulled, so its OS and Python patch version
#     are UNVERIFIED. The base image python:3.12-slim-bookworm is a stand-in with the same minor version.
#   - Harness interpreter: uv-managed CPython 3.13 venv at /opt/h313, with `uv` unpinned (as gen_dry does).
#   - HARNESS_DEPS installed under the requirements.lock.txt constraints, retried unconstrained on
#     failure (as gen_dry does). The lock is read from the /repo mount at container start, because
#     the build context is docker/teacher only. The uv cache is a volume so later runs are fast.
#   - Repo wheelhouse (adk_eval_core, adk_submission, swegemma) installed --no-deps from
#     /repo/wheelhouse at container start, because the repo is mounted and not copied.
#     A missing lock file or wheelhouse is a hard error when a repo checkout is mounted.
#   - gen_dry's import check and its CPU torch / transformers fallback.
#
# Runtime behaviour set by the wrapper (scripts/runs/run_teacher_docker.sh):
#   - HOST_UID/HOST_GID: on exit the entrypoint chowns /repo/logs back to the host user (best effort).
#   - /dev/null is bind-mounted over /repo/.env (only when the host has one) so task_pipeline's
#     load_dotenv() sees nothing; the Anthropic key comes from the shell by name.
#
# Not reproduced (Kaggle-only, GPU, or serving; the student side is replayed from the snapshot in run B):
#   - The pinned Kaggle competition image and its OS packages (see UNVERIFIED above).
#   - /kaggle/input/datasets/metric/gemma-4-developer-agent-wheelhouse and the system-3.12 install
#     of its wheels (starter notebook cell 1). Only the vLLM student server needs them.
#   - GPU/CUDA libraries, the cu128 wheel naming workaround, and the vLLM server (VllmServer).
#   - Kaggle-mounted sandbox inputs such as /kaggle/input/competition-data/wheels. Task wheel caches
#     come from the repo mount (data/assets/task_wheels).
#   - gen_dry's CPU torch/transformers fallback ran only if the import check failed; it was not
#     triggered by the local build, so it is present but unexercised.

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LITELLM_LOCAL_MODEL_COST_MAP=True \
    TRANSFORMERS_NO_TF=1 \
    OTEL_SDK_DISABLED=true

# git: sandbox tasks may call it. The Kaggle image is assumed to have it; this is unverified.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Same steps as gen_dry.py: pip install uv on the system interpreter, then a uv-managed 3.13 venv.
RUN python -m pip install --no-cache-dir -q uv \
 && python -m uv python install 3.13 \
 && python -m uv venv --seed -p 3.13 /opt/h313

# The harness interpreter becomes the default `python` only after the venv exists.
ENV PATH=/opt/h313/bin:/usr/local/bin:/usr/bin:/bin

COPY --chmod=0755 <<'EOF' /usr/local/bin/oec-teacher-entrypoint
#!/bin/sh
# Container start: install the harness env (mirrors gen_dry.py install_py313_harness), run the
# command, then restore host ownership of /repo/logs.
set -eu
PY=/opt/h313/bin/python
REPO=/repo
HARNESS_DEPS="google-adk==1.36.1 litellm python-dotenv pyyaml requests cachetools docker google-genai networkx numpy pydantic rich pandas tokenizers"
CHECK="import sys; sys.path[:0] = ['scripts/runs', 'scripts/traces']; import task_pipeline, run_oec, handoff_llm; from importlib import metadata; print('IMPORT_OK', sys.version.split()[0], metadata.version('swegemma'), metadata.version('adk_submission'), metadata.version('google-adk'), metadata.version('litellm'))"

restore_ownership() {
    rc=$?
    # Best effort: never change the exit status. Only /repo/logs is touched, and -h changes
    # symlinks themselves rather than their targets.
    if [ -n "${HOST_UID:-}" ] && [ -n "${HOST_GID:-}" ] && [ -d "$REPO/logs" ] && [ ! -L "$REPO/logs" ]; then
        chown -R -h "$HOST_UID:$HOST_GID" "$REPO/logs" 2>/dev/null \
            || echo "warning: could not restore ownership of $REPO/logs" >&2
    fi
    exit "$rc"
}
trap restore_ownership EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [ ! -d "$REPO/scripts/runs" ]; then
    echo "oec-teacher: no repo checkout mounted at $REPO; skipping harness setup" >&2
else
    if [ ! -f "$REPO/requirements.lock.txt" ]; then
        echo "oec-teacher: $REPO/requirements.lock.txt is missing; refusing to install harness deps unpinned" >&2
        exit 2
    fi
    if ! ls "$REPO"/wheelhouse/*.whl >/dev/null 2>&1; then
        echo "oec-teacher: no wheels in $REPO/wheelhouse; cannot install the repo harness" >&2
        exit 2
    fi

    grep -v -E '^(\./|#)' "$REPO/requirements.lock.txt" | grep -v -E '^[[:space:]]*$' > /tmp/constraints.txt
    if ! uv pip install --python "$PY" -c /tmp/constraints.txt $HARNESS_DEPS; then
        echo "constrained install failed; retrying unconstrained" >&2
        uv pip install --python "$PY" $HARNESS_DEPS
    fi

    uv pip install --python "$PY" --no-deps --reinstall "$REPO"/wheelhouse/*.whl

    if ! (cd "$REPO" && "$PY" -c "$CHECK"); then
        echo "import check failed; adding transformers/accelerate/safetensors and CPU torch" >&2
        uv pip install --python "$PY" --index-url https://download.pytorch.org/whl/cpu torch torchvision || true
        uv pip install --python "$PY" transformers accelerate safetensors
        (cd "$REPO" && "$PY" -c "$CHECK")
    fi
fi

"$@"
EOF

ENTRYPOINT ["/usr/local/bin/oec-teacher-entrypoint"]
WORKDIR /repo
CMD ["python", "--version"]
