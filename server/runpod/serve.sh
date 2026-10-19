#!/usr/bin/env bash
# Concurrent vLLM for E4B QAT. Weights and the venv live on /workspace.
#
# Optional LoRA adapters from a Hugging Face repo (one subfolder per adapter):
#   LORA_REPO=lonelyforever/gemma4-e4b-qat-lora-adapters-r64-aurora \
#   LORA_ADAPTERS="epoch1 epoch2" bash serve.sh
# Each adapter is served as <LORA_PREFIX>-<subfolder> (default prefix: the
# repo name's last dash-separated word, e.g. aurora-epoch1); the base model
# stays available as gemma4.
set -euo pipefail
ROOT=/workspace
export HF_HOME="$ROOT/hf"
export PATH="$ROOT/.venv/bin:$PATH"
export UPSTREAM_PORT=8002
export CAPTURE_PATH="$ROOT/logs/request_capture.jsonl"
export SUMMARY_PATH="$ROOT/logs/request_summary.jsonl"
MODEL_DIR="$ROOT/models/gemma-4-E4B-it-qat-w4a16-ct"
LORA_REPO="${LORA_REPO:-}"
LORA_ADAPTERS="${LORA_ADAPTERS:-}"
LORA_REPO_TYPE="${LORA_REPO_TYPE:-dataset}"
LORA_MAX_RANK="${LORA_MAX_RANK:-64}"
mkdir -p "$ROOT/logs"

lora_args=()
if [[ -n "$LORA_REPO" ]]; then
  [[ -n "$LORA_ADAPTERS" ]] || { echo "Set LORA_ADAPTERS (e.g. \"epoch1 epoch2\") with LORA_REPO" >&2; exit 1; }
  prefix="${LORA_PREFIX:-${LORA_REPO##*-}}"
  adapter_root="$ROOT/adapters/${LORA_REPO##*/}"
  modules=()
  for name in $LORA_ADAPTERS; do
    if [[ ! -f "$adapter_root/$name/adapter_model.safetensors" || ! -f "$adapter_root/$name/adapter_config.json" ]]; then
      "$ROOT/.venv/bin/hf" download "$LORA_REPO" \
        "$name/adapter_config.json" "$name/adapter_model.safetensors" \
        --repo-type "$LORA_REPO_TYPE" --local-dir "$adapter_root"
    fi
    modules+=("$prefix-$name=$adapter_root/$name")
  done
  read -ra names <<< "$LORA_ADAPTERS"
  lora_args=(--enable-lora --max-lora-rank "$LORA_MAX_RANK" --max-loras "${#names[@]}"
             --lora-modules "${modules[@]}")
  echo "LoRA adapters: ${modules[*]}" | tee -a "$ROOT/logs/vllm.log"
fi

"$ROOT/.venv/bin/vllm" serve "$MODEL_DIR" \
  --served-model-name gemma4 \
  --host 127.0.0.1 --port 8002 \
  --max-model-len 32768 \
  --max-num-seqs 32 \
  --gpu-memory-utilization 0.90 \
  --enable-auto-tool-choice \
  --tool-call-parser gemma4 \
  --reasoning-parser gemma4 \
  ${lora_args[@]+"${lora_args[@]}"} \
  >> "$ROOT/logs/vllm.log" 2>&1 &
vllm_pid=$!
stop() {
  kill "$vllm_pid" 2>/dev/null || true
  wait "$vllm_pid" 2>/dev/null || true
}
trap stop EXIT INT TERM
"$ROOT/.venv/bin/python" "$ROOT/llm_server/capture_proxy.py" >> "$ROOT/logs/proxy.log" 2>&1
