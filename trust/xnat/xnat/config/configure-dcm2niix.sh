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
#

# Exit on first error, undefined variable, or pipe failure.
set -euo pipefail

# This script configures the dcm2niix container service in XNAT.
# It is intended to be run after configure-xnat.sh (which changes the admin password).
#
# Required environment variables:
#   XNAT_ADMIN_USER     - XNAT admin username
#   XNAT_ADMIN_PASSWORD - XNAT admin password (must match what configure-xnat.sh set)
#   DATA_PATH           - Host path for Docker path translation
#

# The below are fixed values for now
XNAT_URL="http://xnat-web:8080" # internal to Docker network
DCM2NIIX_NAME="dcm2niix"

# Wait for XNAT to be available (wall-clock bounded, and each probe carries
# its own timeout, so a dead or wedged XNAT fails the deploy loudly instead
# of printing dots forever).
echo "Waiting for XNAT to be available..."
wait_start=$SECONDS
deadline=$((wait_start + 900))
until curl --output /dev/null --silent --head --fail \
  --connect-timeout 5 --max-time 10 "$XNAT_URL/app/template/Login.vm"; do
  if [[ "$SECONDS" -ge "$deadline" ]]; then
    echo "ERROR: XNAT did not become available within $((SECONDS - wait_start))s" >&2
    exit 1
  fi
  printf '.'
  sleep 1
done
echo "XNAT is up!"

# Helper: curl wrapper for XNAT's REST API. On 2xx, emits the response body
# on stdout for the caller to capture. On any other status — or a curl
# transport failure — reports the request (and, for HTTP failures, the
# response body) on stderr and returns non-zero, which the script's
# `set -e` turns into an abort at the call site (so don't call this inside
# `if`/`||` without handling the failure). Use it for every request to
# XNAT's REST API, reads included: an unnoticed bad GET response is how the
# original empty-CMD_ID deploy failure slipped through — bare `curl -s`
# surfaced neither HTTP errors nor unexpected bodies.
xnat_curl() {
  local response
  local status
  local body
  local curl_exit=0
  response=$(curl -sS --connect-timeout 10 --max-time 120 -w '\n%{http_code}' "$@" \
    -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}") || curl_exit=$?
  if [[ "$curl_exit" -ne 0 ]]; then
    echo "ERROR: curl transport failure (exit $curl_exit)" >&2
    echo "  args: $*" >&2
    return 1
  fi
  status=$(printf '%s' "$response" | tail -n1)
  body=$(printf '%s' "$response" | sed '$d')
  # Strip CRs so a middlebox emitting \r\n line endings can't make the
  # callers' numeric/boolean guards fail on an invisible character (a raw
  # CR is not valid inside JSON strings, so this is lossless).
  status=${status//$'\r'/}
  body=${body//$'\r'/}
  # Fail closed: anything other than a literal 2xx status line (including
  # an empty or non-numeric one) is an error.
  if ! [[ "$status" =~ ^2[0-9]{2}$ ]]; then
    echo "ERROR: XNAT request failed with HTTP $status" >&2
    echo "  args: $*" >&2
    echo "  body: $body" >&2
    return 1
  fi
  printf '%s' "$body"
}

# Path translation for container service plugin
# This is so that the container service can access the data
echo "Adding path translation for container service..."

# Set path-translation-docker-prefix to $DATA_PATH in the backend config
echo "Path translation with DATA_PATH=$DATA_PATH"
backend_config=$(jq --arg data_path "$DATA_PATH" '.["path-translation-docker-prefix"] = $data_path' container-service-backend-configuration.json)

echo "backend_config: $backend_config"
# curl exits 0 on HTTP 4xx/5xx (no --fail), so check the status explicitly — if XNAT
# rejects the new backend config, the ping below could still pass against a previously
# registered backend and mask the failure. "000" = transport error / timeout.
post_status=$(curl -s -o /tmp/docker-server-post.json --max-time 60 -w "%{http_code}" \
  -X POST "$XNAT_URL/xapi/docker/server" \
  -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" \
  -H "Content-Type: application/json" \
  -d "$backend_config") || post_status="000"
if [[ "$post_status" != 2* ]]; then
  echo "ERROR: POST /xapi/docker/server returned HTTP $post_status"
  cat /tmp/docker-server-post.json 2>/dev/null || true
  exit 1
fi

# The Container Service reaches Docker through the xnat-socket-proxy sidecar
# (see docker-compose-stack.yml), not a socket mounted into xnat-web. Fail loudly
# here if that path is broken — the command registration below would still
# "succeed", but every dcm2niix launch would then fail at conversion time.
echo "Pinging Docker through the socket proxy..."
ping_status=$(curl -s -o /dev/null --max-time 60 -w "%{http_code}" "$XNAT_URL/xapi/docker/server/ping" \
  -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}") || ping_status="000"
if [[ "$ping_status" != "200" ]]; then
  echo "ERROR: Container Service cannot reach Docker via xnat-socket-proxy (HTTP $ping_status)"
  exit 1
fi
echo "Container Service -> Docker (via xnat-socket-proxy): OK"

# ----------------------------------------------------------------
# CONTAINER SERVICE
# ----------------------------------------------------------------

echo "Checking if $DCM2NIIX_NAME command exists..."
COMMAND_ID=$(xnat_curl "$XNAT_URL/xapi/commands?name=$DCM2NIIX_NAME" | jq -r '.[0].id // empty')

if [[ -n "$COMMAND_ID" ]]; then
  echo "Found existing command ID: $COMMAND_ID. Deleting..."
  xnat_curl -X DELETE "$XNAT_URL/xapi/commands/$COMMAND_ID" >/dev/null
  echo "Command deleted."
else
  echo "Command not found. Proceeding with addition."
fi

# Add dcm2niix command from json. POST /xapi/commands returns the new
# command's numeric id as the response body — a bare JSON number, not the
# command object (container-service 3.8.1, pinned in trust/xnat/README.md's
# plugin table: CommandRestApi.createCommand still returns ResponseEntity<Long>
# in the 3.8.1 source, and the numeric-id guard below passed live during the
# XNAT 1.10.0 upgrade smoke test).
# Read the id straight from the POST response rather than re-GETting
# /xapi/commands?name=...: on prod that GET came back with no usable
# command even though the POST appeared to succeed (root cause
# undiagnosed), which left CMD_ID unusable and broke the enable/validation
# URLs downstream. The wrapper name is not in the POST response at all, so
# take it from the command definition we just posted.
echo "Adding dcm2niix command..."
CMD_ID=$(xnat_curl -X POST "$XNAT_URL/xapi/commands" \
  -H "Content-Type: application/json" \
  -d @dcm2niix_command.json)

if ! [[ "$CMD_ID" =~ ^[0-9]+$ ]]; then
  echo "ERROR: POST /xapi/commands did not return a numeric command id." >&2
  echo "  body: $CMD_ID" >&2
  exit 1
fi

dcm2niix_wrapper_name=$(jq -r '.xnat[0].name // empty' dcm2niix_command.json)
if [[ -z "$dcm2niix_wrapper_name" ]]; then
  echo "ERROR: no .xnat[0].name wrapper in dcm2niix_command.json." >&2
  exit 1
fi

echo "Command ID: $CMD_ID"
echo "Wrapper Name: $dcm2niix_wrapper_name"

# Enable the dcm2niix command at the site level (makes it available for per-project use)
# See https://wiki.xnat.org/container-service/container-service-api for more details
echo "Enabling $DCM2NIIX_NAME command..."
xnat_curl -X PUT "$XNAT_URL/xapi/commands/$CMD_ID/wrappers/$dcm2niix_wrapper_name/enabled" >/dev/null

# Note: this only enables the command site-wide so it can be used per-project.

# ----------------------------------------------------------------
# EVENT SERVICE
# ----------------------------------------------------------------

# Enable Event Service (required for per-project event subscriptions to work)
echo "Enabling event service..."
xnat_curl -X PUT "$XNAT_URL/xapi/events/prefs" \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}' >/dev/null

# Note: We intentionally do NOT create a site-wide event subscription here.
# Per-project event subscriptions are created by the imaging-api during project creation,
# controlled by the dicom_to_nifti flag. This ensures dcm2niix only auto-triggers
# for projects that have opted in to DICOM-to-NIfTI conversion.

# Clean up any legacy site-wide event subscriptions (from prior versions)
echo "Cleaning up legacy site-wide event subscriptions..."
SUBS=$(xnat_curl "$XNAT_URL/xapi/events/subscriptions")
SITE_SUB_IDS=$(echo "$SUBS" | jq -r '.[] | select(.["project-id"] == null or .["project-id"] == "") | .id')
for SUB_ID in $SITE_SUB_IDS; do
  echo "Deleting site-wide subscription $SUB_ID..."
  xnat_curl -X DELETE "$XNAT_URL/xapi/events/subscription/$SUB_ID" >/dev/null
done

# ----------------------------------------------------------------
# VALIDATION
# ----------------------------------------------------------------

# Every xnat_curl above aborts the script (via set -e) on a non-2xx
# response, so reaching this block means every write was accepted; these
# re-GETs check the resulting state actually persisted. The wrapper-enabled
# endpoint returns a bare JSON boolean (container-service 3.8.1 —
# CommandConfigurationRestApi.isConfigurationEnabled returns a bare Boolean in
# the 3.8.1 source; re-verified live during the XNAT 1.10.0 upgrade smoke test).
echo " "
echo "Validating dcm2niix setup..."
WRAPPER_ENABLED=$(xnat_curl "$XNAT_URL/xapi/commands/$CMD_ID/wrappers/$dcm2niix_wrapper_name/enabled")
if [ "$WRAPPER_ENABLED" != "true" ]; then
  echo "ERROR: dcm2niix wrapper is not enabled site-wide (expected true)" >&2
  echo "  body: $WRAPPER_ENABLED" >&2
  exit 1
fi

# Verify event service is enabled
EVENT_PREFS=$(xnat_curl "$XNAT_URL/xapi/events/prefs")
EVENT_STATUS=$(echo "$EVENT_PREFS" | jq -r '.enabled // empty')
if [ "$EVENT_STATUS" != "true" ]; then
  echo "ERROR: Event service is not enabled (expected true)" >&2
  echo "  body: $EVENT_PREFS" >&2
  exit 1
fi

echo " "
echo "✅ dcm2niix configuration complete and validated!"
