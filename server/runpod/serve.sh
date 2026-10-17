#!/usr/bin/env bash
# Concurrent vLLM for E4B QAT. Weights and the venv live on /workspace.
set -euo pipefail
ROOT=/workspace
export HF_HOME="$ROOT/hf"
export PATH="$ROOT/.venv/bin:$PATH"
export UPSTREAM_PORT=8002
export CAPTURE_PATH="$ROOT/logs/request_capture.jsonl"
export SUMMARY_PATH="$ROOT/logs/request_summary.jsonl"
MODEL_DIR="$ROOT/models/gemma-4-E4B-it-qat-w4a16-ct"
mkdir -p "$ROOT/logs"

"$ROOT/.venv/bin/vllm" serve "$MODEL_DIR" \
  --served-model-name gemma4 \
  --host 127.0.0.1 --port 8002 \
  --max-model-len 32768 \
  --max-num-seqs 32 \
  --gpu-memory-utilization 0.90 \
  --enable-auto-tool-choice \
  --tool-call-parser gemma4 \
  --reasoning-parser gemma4 \
  >> "$ROOT/logs/vllm.log" 2>&1 &
vllm_pid=$!
stop() {
  kill "$vllm_pid" 2>/dev/null || true
  wait "$vllm_pid" 2>/dev/null || true
}
trap stop EXIT INT TERM
"$ROOT/.venv/bin/python" "$ROOT/llm_server/capture_proxy.py" >> "$ROOT/logs/proxy.log" 2>&1
