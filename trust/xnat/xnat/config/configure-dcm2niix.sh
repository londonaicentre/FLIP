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

# Path translation for container service plugin
# This is so that the container service can access the data
echo "Adding path translation for container service..."

# Replace ${DATA_PATH} in the file and store in a variable
echo "Path translation with DATA_PATH=$DATA_PATH"
backend_config=$(jq --arg data_path "$DATA_PATH" '.["path-translation-docker-prefix"] = $data_path' container-service-backend-configuration.json)

echo "backend_config: $backend_config"
curl -s -X POST "$XNAT_URL/xapi/docker/server" \
  -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" \
  -H "Content-Type: application/json" \
  -d "$backend_config"

# The Container Service reaches Docker through the xnat-socket-proxy sidecar
# (see docker-compose-stack.yml), not a socket mounted into xnat-web. Fail loudly
# here if that path is broken — the command registration below would still
# "succeed", but every dcm2niix launch would then fail at conversion time.
echo "Pinging Docker through the socket proxy..."
ping_status=$(curl -s -o /dev/null -w "%{http_code}" "$XNAT_URL/xapi/docker/server/ping" \
  -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}")
if [[ "$ping_status" != "200" ]]; then
  echo "ERROR: Container Service cannot reach Docker via xnat-socket-proxy (HTTP $ping_status)"
  exit 1
fi
echo "Container Service -> Docker (via xnat-socket-proxy): OK"

# ----------------------------------------------------------------
# CONTAINER SERVICE
# ----------------------------------------------------------------

echo "Checking if $DCM2NIIX_NAME command exists..."
COMMAND_ID=$(curl -s "$XNAT_URL/xapi/commands?name=$DCM2NIIX_NAME" \
  -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" | jq -r '.[0].id')

if [[ "$COMMAND_ID" != "null" && -n "$COMMAND_ID" ]]; then
  echo "Found existing command ID: $COMMAND_ID. Deleting..."
  curl -s -X DELETE "$XNAT_URL/xapi/commands/$COMMAND_ID" \
    -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}"
  echo "Command deleted."
else
  echo "Command not found. Proceeding with addition."
fi

# Add dcm2niix command from json
echo "Adding dcm2niix command..."
curl -s -X POST "$XNAT_URL/xapi/commands" \
  -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" \
  -H "Content-Type: application/json" \
  -d @dcm2niix_command.json

# Get the command ID for dcm2niix with improved extraction
echo " "
echo "Getting command ID for $DCM2NIIX_NAME..."
RESPONSE=$(curl -s "$XNAT_URL/xapi/commands?name=$DCM2NIIX_NAME" \
  -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}")

echo "$RESPONSE"

# Extract the id from the first element in the JSON array
CMD_ID=$(echo "$RESPONSE" | jq -r '.[0].id')
echo "Command ID: $CMD_ID"

# Grab the event name e.g. "xnat":\[{"name":"dcm2niix-scan"\}
dcm2niix_wrapper_name=$(echo "$RESPONSE" | jq -r '.[0].xnat[0].name')
echo "Wrapper Name: $dcm2niix_wrapper_name"

# Enable the dcm2niix command at the site level (makes it available for per-project use)
# See https://wiki.xnat.org/container-service/container-service-api for more details
echo "Enabling $DCM2NIIX_NAME command..."
curl -s -X PUT "$XNAT_URL/xapi/commands/$CMD_ID/wrappers/$dcm2niix_wrapper_name/enabled" \
  -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}"

# Note: this only enables the command site-wide so it can be used per-project.

# ----------------------------------------------------------------
# EVENT SERVICE
# ----------------------------------------------------------------

# Enable Event Service (required for per-project event subscriptions to work)
echo "Enabling event service..."
curl -s -X PUT "$XNAT_URL/xapi/events/prefs" \
  -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}'

# Note: We intentionally do NOT create a site-wide event subscription here.
# Per-project event subscriptions are created by the imaging-api during project creation,
# controlled by the dicom_to_nifti flag. This ensures dcm2niix only auto-triggers
# for projects that have opted in to DICOM-to-NIfTI conversion.

# Clean up any legacy site-wide event subscriptions (from prior versions)
echo "Cleaning up legacy site-wide event subscriptions..."
SUBS=$(curl -s "$XNAT_URL/xapi/events/subscriptions" \
  -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}")
SITE_SUB_IDS=$(echo "$SUBS" | jq -r '.[] | select(.["project-id"] == null or .["project-id"] == "") | .id')
for SUB_ID in $SITE_SUB_IDS; do
  echo "Deleting site-wide subscription $SUB_ID..."
  curl -s -X DELETE "$XNAT_URL/xapi/events/subscription/$SUB_ID" \
    -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}"
done

# ----------------------------------------------------------------
# VALIDATION
# ----------------------------------------------------------------

# Verify dcm2niix command was registered and enabled
echo " "
echo "Validating dcm2niix setup..."
VALIDATION=$(curl -s -o /dev/null -w "%{http_code}" "$XNAT_URL/xapi/commands/$CMD_ID/wrappers/$dcm2niix_wrapper_name/enabled" \
  -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}")
if [ "$VALIDATION" != "200" ]; then
  echo "ERROR: dcm2niix command enable check returned HTTP $VALIDATION (expected 200)"
  exit 1
fi

# Verify event service is enabled
EVENT_STATUS=$(curl -s "$XNAT_URL/xapi/events/prefs" -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" | jq -r '.enabled')
if [ "$EVENT_STATUS" != "true" ]; then
  echo "ERROR: Event service is not enabled (expected true, got $EVENT_STATUS)"
  exit 1
fi

echo " "
echo "✅ dcm2niix configuration complete and validated!"
