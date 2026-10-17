#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
start_service() {
  local name="$1"
  if [[ -f "$name.pid" ]] && kill -0 "$(cat "$name.pid")" 2>/dev/null; then
    echo "$name is already running."
  else
    nohup "./$name.sh" >> "$name.log" 2>&1 < /dev/null &
    echo $! > "$name.pid"
  fi
}
# Keep the existing server log and PID filenames.
if ! curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then
  if [[ -f server.pid ]] && kill -0 "$(cat server.pid)" 2>/dev/null; then
    echo "Server is starting."
  else
    nohup ./serve.sh >> server.log 2>&1 < /dev/null &
    echo $! > server.pid
  fi
fi
start_service ngrok
echo "Waiting for the server (first startup can take several minutes)..."
for ((i=0; i<120; i++)); do
  if curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then
    echo "Server ready."
    echo "Direct API: http://10.55.1.6:8000/v1"
    echo "On server: http://127.0.0.1:8000/v1"
    python3 - <<'PY'
import json, time, urllib.request
for _ in range(10):
    try:
        with urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=3) as response:
            tunnels = json.load(response)["tunnels"]
        if tunnels:
            print("Cross-device API: " + tunnels[0]["public_url"] + "/v1")
            break
    except Exception:
        pass
    time.sleep(1)
else:
    raise SystemExit("ngrok URL unavailable; check ngrok.log")
PY
    echo "Requests require Authorization: Bearer YOUR_VLLM_API_KEY"
    exit 0
  fi
  sleep 5
done
echo "Startup timed out; check server.log and ngrok.log." >&2
exit 1
