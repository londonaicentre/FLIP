#!/bin/bash
#
# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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

# Provision a new client for an existing NVFLARE federated learning network
# Usage: ./scripts/provision-additional-client.sh <net_number> [fl_port] [admin_port]
# 
# You'll need to have added a new Trust_<N> client entry in the net-<net_number>_project.yml file
# before running this script.
# 
# After running this script, remember to add the new client service to your docker compose file.
set -e

NET_NUMBER="${1:?Error: NET_NUMBER is required}"
FL_PORT="${2:-8002}"
ADMIN_PORT="${3:-8003}"

WORKSPACE="workspace/net-${NET_NUMBER}"
FL_SERVICES="workspace/net-${NET_NUMBER}/services"
PROJECT_YML="net-${NET_NUMBER}_project.yml"

# Other configurations
VERBOSE="true"

log() { echo "$*"; }
vlog() { if [[ "${VERBOSE}" == "true" ]]; then echo "   [verbose] $*"; fi }

# 1. Regenerate the net-specific project YAML
uv run nvflare provision -p "$PROJECT_YML"

#3. Find the latest prod directory
PROD_DIR=$(ls -d "$WORKSPACE"/prod_* 2>/dev/null | sort | tail -n 1)
if [ -z "$PROD_DIR" ]; then
  echo "No prod directory found in $WORKSPACE"
  exit 1
fi

# List all client folders in latest prod directory
PROD_CLIENTS=( $(find "$PROD_DIR" -maxdepth 1 -type d -name 'Trust_*') )
# List all client folders in fl_services/net-N
FL_SERVICES_CLIENTS=( $(find "$FL_SERVICES" -maxdepth 1 -type d -name 'Trust_*') )

# Debug output
echo "[DEBUG] Latest prod dir: $PROD_DIR"
echo "[DEBUG] workspace/services dir: $FL_SERVICES"
echo "[DEBUG] Clients in prod: ${PROD_CLIENTS[@]}"
echo "[DEBUG] Clients in workspace/services: ${FL_SERVICES_CLIENTS[@]}"

# 5. For each client in prod, copy only if not already present in fl_services
SERVER_DIR="workspace/net-${NET_NUMBER}/services/fl-server-net-${NET_NUMBER}"

for CLIENT_DIR in "${PROD_CLIENTS[@]}"; do
  [ -d "$CLIENT_DIR" ] || continue
  CLIENT_NAME=$(basename "$CLIENT_DIR")
  DEST_DIR="$FL_SERVICES/$CLIENT_NAME"
  if [ ! -d "$DEST_DIR" ]; then
    echo "[DEBUG] New client detected: $CLIENT_NAME"
    
    # Copy the entire client directory from prod to fl_services
    cp -r "$CLIENT_DIR" "$FL_SERVICES/"
    echo "[DEBUG] Copied $CLIENT_NAME to $DEST_DIR"
    
    # Fix start.sh to run in foreground (remove & from sub_start.sh call)
    start_script="$DEST_DIR/startup/start.sh"
    if [[ -f "${start_script}" ]]; then
        vlog "Modifying start.sh script to run 'sub_start.sh' process in foreground (removing &)"
        sed -i 's|\$DIR/sub_start.sh &|\$DIR/sub_start.sh|g' "${start_script}"
    fi

    # Overwrite rootCA.pem in new client with the one from the server
    cp "$SERVER_DIR/startup/rootCA.pem" "$DEST_DIR/startup/rootCA.pem"

    echo "Copied new client '$CLIENT_NAME' and overwrote rootCA.pem from server."
    echo "Add the new client '$CLIENT_NAME' to your docker compose file if needed."

    # NOTE the below is difficult to maintain, so commented out for now. We could consider copying an existing service
    # definition and modifying it accordingly, but for now we leave it to the user to add new clients to their compose
    # files as needed.

    # Add client to docker compose if not present
#     COMPOSE_FILE="deploy/compose-net-${NET_NUMBER}.yml"
#     if ! grep -q "${CLIENT_NAME}:" "$COMPOSE_FILE"; then
#       echo "[DEBUG] Appending $CLIENT_NAME service to $COMPOSE_FILE"
#       cat <<EOF >> "$COMPOSE_FILE"

#   ${CLIENT_NAME}:
#     container_name: ${CLIENT_NAME}
#     image: fl-base:dev
#     environment:
#       - NET_ID=net-${NET_NUMBER}
#       - LOCAL_DEV=false
#       - MIN_CLIENTS=1
#       - IMAGES_DIR=/app/data/images
#       - UPLOADED_FEDERATED_DATA_BUCKET=fake-s3-bucket
#       # - NUM_AVAILABLE_GPUS=1
#       # - MEMORY_PER_GPU_IN_GIB=16
#       # - LOG_LEVEL=DEBUG
#     volumes:
#       - ../workspace/services/net-${NET_NUMBER}/${CLIENT_NAME}/local:/app/local
#       - ../workspace/services/net-${NET_NUMBER}/${CLIENT_NAME}/startup:/app/startup
#       - ../workspace/services/net-${NET_NUMBER}/${CLIENT_NAME}/transfer:/app/transfer
#     command: ["/bin/bash", "/app/entrypoint.sh"]
#     shm_size: "32gb"
#     gpus: all
# EOF
#         else
#           echo "[DEBUG] $CLIENT_NAME already present in $COMPOSE_FILE, skipping append."
#         fi
  else
    echo "[DEBUG] Client already exists in fl_services: $CLIENT_NAME (skipping)"
  fi
done