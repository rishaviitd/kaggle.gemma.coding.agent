#!/usr/bin/env bash
# Persistent install on the RunPod network volume. Safe to rerun.
set -euo pipefail
ROOT=/workspace
MODEL_ID="${MODEL_ID:-google/gemma-4-E4B-it-qat-w4a16-ct}"
MODEL_DIR="$ROOT/models/gemma-4-E4B-it-qat-w4a16-ct"
export HF_HOME="$ROOT/hf"
export PATH="$ROOT/.venv/bin:$PATH"

mkdir -p "$ROOT/models" "$ROOT/logs" "$ROOT/llm_server" "$HF_HOME"
if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  python3 -m venv "$ROOT/.venv"
fi
"$ROOT/.venv/bin/pip" install -U pip
"$ROOT/.venv/bin/pip" install vllm huggingface_hub
if [[ ! -f "$MODEL_DIR/config.json" ]]; then
  "$ROOT/.venv/bin/hf" download "$MODEL_ID" --local-dir "$MODEL_DIR"
fi
echo "Ready: $MODEL_DIR"
