#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
exec .venv/bin/python scripts/runs/run_pass3.py \
  --split val --submission src/rishav/inspiration-one \
  --provider openai --model openai/responses/gpt-6-luna \
  --api-base https://api.openai.com/v1 \
  --run-name luna-inspiration-one-pass3-responses-20261017 \
  --task-ids \
  fastapi_12942 fastapi_14372 fastapi_14794 fastapi_5624 fastapi_14077 \
  fastapi_14266 fastapi_14246 fastapi_14482 fastapi_14419 fastapi_14306 \
  rich_3043 rich_3905 rich_4076 rich_3506 rich_3935 rich_3052 rich_3180 \
  requests_7205 requests_7427 httpx_3672 "$@"
