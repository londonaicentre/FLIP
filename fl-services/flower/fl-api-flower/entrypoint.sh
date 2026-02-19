#!/usr/bin/env bash
set -euo pipefail

mkdir -p /home/app/.flwr
cat >/home/app/.flwr/config.toml <<EOF
[superlink.local]
address = "${SUPERLINK_ADDRESS:-superlink:9093}"
insecure = true
EOF

exec uv run python -m uvicorn fl_api.app:app --host 0.0.0.0 --port 8000 --reload --reload-dir /app/fl_api
