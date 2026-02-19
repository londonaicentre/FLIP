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

# Provision an NVFLARE federated learning network
# Usage: ./scripts/provision-network.sh <net_number> [fl_port] [admin_port]

set -euo pipefail

NET_NUMBER="${1:?Error: NET_NUMBER is required}"
FL_PORT="${2:-8002}"
ADMIN_PORT="${3:-8003}"
DEBUG="${4:-false}"

# Other configurations
VERBOSE="true"

log() { echo "$*"; }
vlog() { if [[ "${VERBOSE}" == "true" ]]; then echo "   [verbose] $*"; fi }

WORKSPACE_DIR="workspace/net-${NET_NUMBER}"
SERVICES_DIR="${WORKSPACE_DIR}/services"

log "Provisioning network ${NET_NUMBER}..."

# FLARE project YAML file for this network
PROJECT_YAML="net-${NET_NUMBER}_project.yml"

# Generate network-specific project file from template
export NET_NUMBER FL_PORT ADMIN_PORT DEBUG
envsubst < net_project.yml > "${PROJECT_YAML}"

# Run NVFLARE provisioning
uv run nvflare provision -p "${PROJECT_YAML}"

echo "Restructuring provisioned files in workspace..."

# Find the prod directory created by nvflare provision (the last one if multiple exist)
PROD_DIR=$(find "${WORKSPACE_DIR}" -name "prod_*" -type d | tail -n 1)

if [[ -z "${PROD_DIR}" ]]; then
    echo "Error: Could not find prod directory in ${WORKSPACE_DIR}"
    exit 1
fi

rm -rf "${SERVICES_DIR}"
mkdir -p "${SERVICES_DIR}"

# Function to get the first participant name of a given type from the project YAML
# This avoids hardcoding participant (server, admin) names in the script
get_participant_name_by_type() {
  local project_yaml="$1"
  local participant_type="$2"

  yq -r \
    ".participants[] | select(.type == \"${participant_type}\") | .name" \
    "$project_yaml" | head -n 1
}

# Function to restructure a participant's files
restructure_participant() {
    local participant_name="$1"
    local participant_name_dest="$2"
    local is_client="$3"

    local src_path="${PROD_DIR}/${participant_name}"
    local dest_path="${SERVICES_DIR}/${participant_name_dest}"

    echo " - Restructuring ${participant_name}"

    mkdir -p "${dest_path}"

    # Move standard directories
    for dir in startup local transfer; do
        if [[ -d "${src_path}/${dir}" ]]; then
            vlog "Moving '${src_path}/${dir}' to '${dest_path}/'"
            mv "${src_path}/${dir}" "${dest_path}/"
        fi
    done

    # Move readme.txt
    if [[ -f "${src_path}/readme.txt" ]]; then
        vlog "Moving 'readme.txt' to '${dest_path}/'"
        mv "${src_path}/readme.txt" "${dest_path}/"
    fi

    # Fix start.sh to run in foreground (remove & from sub_start.sh call)
    local start_script="${dest_path}/startup/start.sh"
    if [[ -f "${start_script}" ]]; then
        vlog "Modifying start.sh script to run 'sub_start.sh' process in foreground (removing &)"
        sed -i 's|\$DIR/sub_start.sh &|\$DIR/sub_start.sh|g' "${start_script}"
    fi

    # Create log_config.template.json
    local local_dir="${dest_path}/local"
    if [[ -d "${local_dir}" ]]; then
        create_log_config_template "${local_dir}"
    fi

    # Create resources template configs (FL clients only)
    if [[ -d "${local_dir}" && "${is_client}" == "1" ]]; then
        create_resources_template "${local_dir}"
    fi
}

# Create log_config.template.json from log_config.json.default
create_log_config_template() {
    local local_dir="$1"
    local default_file="log_config.json.default"
    local template_file="log_config.template.json"
    local src="${local_dir}/${default_file}"
    local dest="${local_dir}/${template_file}"

    if [[ ! -f "${src}" ]]; then
        vlog "File ${src} not found, skipping"
        return
    fi
    vlog "Creating ${template_file}"

    # Replace "level": "INFO" with "level": "${LOG_LEVEL}"
    sed 's|"level"[[:space:]]*:[[:space:]]*"INFO"|"level": "${LOG_LEVEL}"|g' \
        "${src}" > "${dest}"
}

# Create resources.template.json from resources.json.default (FL clients only)
create_resources_template() {
    local local_dir="$1"
    local default_file="resources.json.default"
    local template_file="resources.template.json"
    local src="${local_dir}/${default_file}"
    local dest="${local_dir}/${template_file}"

    if [[ ! -f "${src}" ]]; then
        vlog "File ${src} not found, skipping"
        return
    fi
    vlog "Creating ${template_file}"

    # Replace num_of_gpus and mem_per_gpu_in_GiB with template variables
    sed -E '
      s|"num_of_gpus"[[:space:]]*:[[:space:]]*[0-9]+|"num_of_gpus": ${NUM_AVAILABLE_GPUS}|g;
      s|"mem_per_gpu_in_GiB"[[:space:]]*:[[:space:]]*[0-9]+|"mem_per_gpu_in_GiB": ${MEMORY_PER_GPU_IN_GIB}|g
    ' "${src}" > "${dest}"
}


# Restructure all participants
SERVER_NAME="$(get_participant_name_by_type "$PROJECT_YAML" server)"
echo "Identified server participant name: ${SERVER_NAME}"

ADMIN_NAME="$(get_participant_name_by_type "$PROJECT_YAML" admin)"
echo "Identified admin participant name: ${ADMIN_NAME}"

# TODO this is hardcoded to 2 clients for now -- make this dynamic later
restructure_participant "Trust_1" "Trust_1" 1
restructure_participant "Trust_2" "Trust_2" 1
restructure_participant "${SERVER_NAME}" "fl-server-net-${NET_NUMBER}" 0
restructure_participant "${ADMIN_NAME}" "flip-fl-api-net-${NET_NUMBER}" 0

# Clean up prod directory
echo "Cleaning up prod directory..."
vlog "Removing directory: ${PROD_DIR}"
rm -rf "${PROD_DIR}"

echo ""
echo "Network ${NET_NUMBER} provisioned successfully"
echo "  Secrets are in: ${SERVICES_DIR}/ (gitignored)"
echo "  Run 'make up NET_NUMBER=${NET_NUMBER}' to start the network"
