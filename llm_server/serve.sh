#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
set -a
source .env
set +a
export PATH=/datadrive/llm_inference/.venv/bin:$PATH
export CUDA_HOME=/usr/local/cuda
export PATH=/usr/local/cuda/bin:$PATH
export HF_HOME=/datadrive/llm_inference/hf-cache

/datadrive/llm_inference/.venv/bin/vllm serve /datadrive/models/gemma-4-12B-it-qat-w4a16-ct \
  --served-model-name gemma4 \
  --host 127.0.0.1 --port 8001 \
  --max-model-len 32768 --max-num-seqs 4 \
  --gpu-memory-utilization 0.60 \
  --enable-auto-tool-choice \
  --tool-call-parser gemma4 \
  --reasoning-parser gemma4 &
vllm_pid=$!
stop() {
  kill "$vllm_pid" 2>/dev/null || true
  wait "$vllm_pid" 2>/dev/null || true
}
trap stop EXIT INT TERM
/datadrive/llm_inference/.venv/bin/python capture_proxy.py &
proxy_pid=$!
wait "$proxy_pid"
