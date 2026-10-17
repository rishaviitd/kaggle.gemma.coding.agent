#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
for service in ngrok proxy ssh; do
  label="com.pwm.gemma-tunnel.$service"
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
done
echo "Laptop tunnel stopped."
