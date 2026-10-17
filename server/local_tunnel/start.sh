#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

mode="${1:---local}"
case "$mode" in
  --local|local) mode=local ;;
  --ngrok|ngrok) mode=ngrok ;;
  *) echo "Usage: $0 [--local|--ngrok]" >&2; exit 2 ;;
esac

set -a
source .env
set +a
: "${VLLM_API_KEY:?Set VLLM_API_KEY in .env}"
if [[ "$mode" == ngrok ]]; then
  : "${NGROK_AUTHTOKEN:?Set NGROK_AUTHTOKEN in .env}"
fi

runtime="$HOME/Library/Application Support/gemma-tunnel"
mkdir -p "$runtime/bin" "$HOME/.ssh"
cp .env "$runtime/.env"
cp ngrok-agent.sh "$runtime/ngrok-agent.sh"
cp bin/ngrok "$runtime/bin/ngrok"
cp proxy.py "$runtime/proxy.py"
if [[ ! -f "$HOME/.ssh/gemma_h100_key" ]]; then
  echo "Missing SSH key: $HOME/.ssh/gemma_h100_key" >&2
  exit 1
fi
chmod 600 "$runtime/.env" "$HOME/.ssh/gemma_h100_key"
chmod 700 "$runtime/ngrok-agent.sh" "$runtime/bin/ngrok"
if [[ ! -e requests.log && ! -L requests.log ]]; then
  ln -s "$runtime/requests.log" requests.log
fi

server_dir=/home/azureuser/VoxCPM/rishav_experiments/llm_inference
echo "Checking H100 vLLM status..."
start_log_line=$(ssh 10.55.1.6 "cd '$server_dir' && wc -l < server.log" 2>/dev/null || echo 0)
if ssh 10.55.1.6 "curl -fsS http://127.0.0.1:8001/health >/dev/null 2>&1"; then
  echo "H100 vLLM is already healthy."
else
  echo "H100 vLLM is stopped; starting the model server..."
  ssh 10.55.1.6 "cd '$server_dir' && if test -f server.pid; then old_pid=\$(cat server.pid); kill \$old_pid 2>/dev/null || true; fi; proxy_pids=\$(pgrep -f '^/datadrive/llm_inference/.venv/bin/python capture_proxy.py$' || true); if test -n \"\$proxy_pids\"; then kill \$proxy_pids 2>/dev/null || true; fi; nohup ./serve.sh >> server.log 2>&1 < /dev/null & echo \$! > server.pid"
fi

service_domain="gui/$(id -u)"
services=(ssh proxy)
if [[ "$mode" == ngrok ]]; then
  services+=(ngrok)
else
  launchctl bootout "$service_domain/com.pwm.gemma-tunnel.ngrok" 2>/dev/null || true
fi
for service in "${services[@]}"; do
  label="com.pwm.gemma-tunnel.$service"
  if ! launchctl print "$service_domain/$label" >/dev/null 2>&1; then
    launchctl bootstrap "$service_domain" "$PWD/$service.plist"
  fi
done

echo "Waiting for vLLM and the $mode tunnel (model loading can take a few minutes)..."
last_progress=""
for ((attempt=0; attempt<120; attempt++)); do
  progress=$(ssh 10.55.1.6 "cd '$server_dir' && tail -n +$((start_log_line + 1)) server.log | grep -aE 'Loading safetensors checkpoint shards|Loading model weights|Model loading took|warmup|Compiling a graph|GPU KV cache size|Application startup complete|ERROR' | tail -n 1" 2>/dev/null || true)
  if [[ -n "$progress" && "$progress" != "$last_progress" ]]; then
    printf 'H100: %s\n' "$progress"
    last_progress="$progress"
  fi
  if (( attempt > 0 && attempt % 6 == 0 )); then
    latest_log=$(ssh 10.55.1.6 "cd '$server_dir' && tail -n +$((start_log_line + 1)) server.log | tr '\\r' '\\n' | tail -n 1" 2>/dev/null || true)
    if [[ -n "$latest_log" ]]; then
      printf 'Still starting (%ds). Latest H100 log: %s\n' "$((attempt * 5))" "$latest_log"
    else
      printf 'Still starting (%ds); waiting for fresh H100 startup logs...\n' "$((attempt * 5))"
    fi
  fi
  if curl -fsS http://127.0.0.1:18001/health >/dev/null 2>&1; then
    if [[ "$mode" == local ]]; then
      echo "vLLM and local tunnel are ready."
      echo "Laptop URL: http://127.0.0.1:18001/v1"
      echo "Request log: $PWD/requests.log"
      echo 'Use Authorization: Bearer <VLLM_API_KEY>.'
      exit 0
    fi
    public_url=$(python3 - <<'PY'
import json
import urllib.request
try:
    with urllib.request.urlopen('http://127.0.0.1:4040/api/tunnels', timeout=3) as response:
        print(json.load(response)['tunnels'][0]['public_url'])
except (OSError, KeyError, IndexError):
    pass
PY
    )
    if [[ -n "$public_url" ]]; then
      echo "vLLM and tunnel are ready."
      echo "Laptop URL: http://127.0.0.1:18001/v1"
      echo "Other laptop URL: $public_url/v1"
      echo "Request log: $PWD/requests.log"
      echo 'Use Authorization: Bearer <VLLM_API_KEY> on both.'
      exit 0
    fi
  fi
  sleep 5
done
echo "Startup timed out. Check /tmp/gemma-tunnel-{ssh,ngrok}.log and remote server.log." >&2
exit 1
