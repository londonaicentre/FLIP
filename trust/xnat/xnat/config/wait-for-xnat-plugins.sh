#!/bin/bash
#
# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

set -euo pipefail

: "${XNAT_ADMIN_USER:?}" "${XNAT_ADMIN_INITIAL_PASSWORD:?}" "${XNAT_ADMIN_PASSWORD:?}"

XNAT_URL="${XNAT_URL:-http://xnat-web:8080}"
READINESS_URL="${XNAT_URL}/xapi/dqr/settings"
TIMEOUT_SECONDS="${XNAT_PLUGIN_READINESS_TIMEOUT_SECONDS:-900}"
POLL_SECONDS="${XNAT_PLUGIN_READINESS_POLL_SECONDS:-5}"

if ! [[ "$TIMEOUT_SECONDS" =~ ^[0-9]+$ && "$POLL_SECONDS" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
  echo "ERROR: XNAT plugin readiness timeout/poll values must be non-negative numbers." >&2
  exit 1
fi

probe() {
  local password="$1"
  local status
  status=$(curl -sS -o /dev/null -w '%{http_code}' \
    --connect-timeout 5 --max-time 10 \
    -u "${XNAT_ADMIN_USER}:${password}" "$READINESS_URL") || status="000"
  printf '%s' "$status"
}

echo "Waiting for the XNAT DQR plugin to be ready..."
wait_start=$SECONDS
last_status="000"
while true; do
  last_status=$(probe "$XNAT_ADMIN_INITIAL_PASSWORD")
  if [[ "$last_status" != 2* && "$XNAT_ADMIN_PASSWORD" != "$XNAT_ADMIN_INITIAL_PASSWORD" ]]; then
    last_status=$(probe "$XNAT_ADMIN_PASSWORD")
  fi

  if [[ "$last_status" == 2* ]]; then
    echo "XNAT DQR plugin is ready (HTTP $last_status)."
    exit 0
  fi

  if (( SECONDS - wait_start >= TIMEOUT_SECONDS )); then
    echo "ERROR: XNAT DQR plugin did not become ready within $((SECONDS - wait_start))s." >&2
    echo "  endpoint: $READINESS_URL" >&2
    echo "  last HTTP status: $last_status" >&2
    exit 1
  fi

  echo "  DQR not ready yet (HTTP $last_status); retrying..."
  sleep "$POLL_SECONDS"
done
