#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
set -a
source .env
set +a
exec ./bin/ngrok http 127.0.0.1:18001 --log stdout
