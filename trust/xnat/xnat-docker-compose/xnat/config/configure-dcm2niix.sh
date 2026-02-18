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


# This script configures the dcm2niix container service in XNAT
# It is intended to be run after the XNAT container has been started
# for the first time. 
# 
# This needs that configure-xnat.sh has been run, otherwise it will fail
# because the password of the admin user has not been changed yet. 
# 
# Note the rest of the environment variables are available from the file
# - trust/xnat/xnat-docker-compose/.env
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

# Enable the dcm2niix command
echo "Enabling $DCM2NIIX_NAME command..."
curl -s -X PUT "$XNAT_URL/xapi/commands/$CMD_ID/wrappers/$dcm2niix_wrapper_name/enabled" \
  -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}"

# Keep in mind that this does not enable the command at the project level
# This should be done in the imaging-api when a project is created
# But I think the default is that it will be enabled at the project level

# Replace ${CMD_ID} in the file and store in a variable
dcm2niix_event=$(sed "s/\${CMD_ID}/$CMD_ID/g" dcm2niix_event.json)
echo "dcm2niix_event: $dcm2niix_event"

# ----------------------------------------------------------------
# EVENT SERVICE
# ----------------------------------------------------------------

# Enable Event Service
echo "Enabling event service..."
curl -s -X PUT "$XNAT_URL/xapi/events/prefs" \
  -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}'

# Create event subscription for scan created
echo "Creating event subscription for dcm2niix..."
curl -s -X POST "$XNAT_URL/xapi/events/subscription" \
  -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" \
  -H "Content-Type: application/json" \
  -d "$dcm2niix_event"

echo " "
echo "XNAT configuration complete!"
