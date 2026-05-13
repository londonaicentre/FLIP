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

# Wait for XNAT to be available
echo "Waiting for XNAT to be available..."
until $(curl --output /dev/null --silent --head --fail $XNAT_URL/app/template/Login.vm); do
  printf '.'
  sleep 1
done
echo "XNAT is up!"

# Helper: curl that prints body, then exits with a clear error if the HTTP
# status code is not 2xx. Solves the silent-failure problem where `curl -s`
# discarded a 4xx/5xx body and the script kept going with empty state. Use
# this for every write to XNAT's REST API.
xnat_curl() {
  local response
  local status
  response=$(curl -sS -w '\n%{http_code}' "$@" -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}")
  status=$(printf '%s' "$response" | tail -n1)
  body=$(printf '%s' "$response" | sed '$d')
  if [[ "$status" -lt 200 || "$status" -ge 300 ]]; then
    echo "ERROR: XNAT request failed with HTTP $status" >&2
    echo "  args: $*" >&2
    echo "  body: $body" >&2
    return 1
  fi
  # Body is exported on stdout for the caller to consume.
  printf '%s' "$body"
}

# Path translation for container service plugin
# This is so that the container service can access the data
echo "Adding path translation for container service..."

# Replace ${DATA_PATH} in the file and store in a variable
echo "Path translation with DATA_PATH=$DATA_PATH"
backend_config=$(jq --arg data_path "$DATA_PATH" '.["path-translation-docker-prefix"] = $data_path' container-service-backend-configuration.json)

echo "backend_config: $backend_config"
xnat_curl -X POST "$XNAT_URL/xapi/docker/server" \
  -H "Content-Type: application/json" \
  -d "$backend_config" >/dev/null

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

# Add dcm2niix command from json. POST /xapi/commands returns the created
# command including its `id`; extract it directly to avoid the
# eventual-consistency race the previous re-GET ran into (the GET could
# return an empty array even after the POST succeeded, leaving the rest of
# the script with empty CMD_ID and the validation curl 500ing on an
# invalid URL).
echo "Adding dcm2niix command..."
POST_RESPONSE=$(xnat_curl -X POST "$XNAT_URL/xapi/commands" \
  -H "Content-Type: application/json" \
  -d @dcm2niix_command.json)
echo "POST response: $POST_RESPONSE"

CMD_ID=$(echo "$POST_RESPONSE" | jq -r '.id // empty')
dcm2niix_wrapper_name=$(echo "$POST_RESPONSE" | jq -r '.xnat[0].name // empty')

if [[ -z "$CMD_ID" || -z "$dcm2niix_wrapper_name" ]]; then
  echo "ERROR: POST /xapi/commands did not return an id + xnat[0].name." >&2
  echo "  body: $POST_RESPONSE" >&2
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

# Verify dcm2niix command was registered and enabled. The xnat_curl helper
# above already exits non-zero on any failure, so reaching this block means
# the writes succeeded; this is a belt-and-braces check that the enable
# state is queryable.
echo " "
echo "Validating dcm2niix setup..."
xnat_curl "$XNAT_URL/xapi/commands/$CMD_ID/wrappers/$dcm2niix_wrapper_name/enabled" >/dev/null

# Verify event service is enabled
EVENT_STATUS=$(xnat_curl "$XNAT_URL/xapi/events/prefs" | jq -r '.enabled')
if [ "$EVENT_STATUS" != "true" ]; then
  echo "ERROR: Event service is not enabled (expected true, got $EVENT_STATUS)"
  exit 1
fi

echo " "
echo "✅ dcm2niix configuration complete and validated!"
