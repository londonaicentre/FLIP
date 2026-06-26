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
# Usage: ./deploy/providers/nvflare/scripts/provision-network.sh <project_yaml_file> <net_number> [workspace_parent_dir]

# Fail fast and loud: abort on any command error (-e), on an unset variable (-u),
# and on a failure anywhere in a pipeline (-o pipefail). Without this a non-zero
# `nvflare provision` (or `yq`) could leave partial output yet let the script
# carry on restructuring and report success. Mirrors provision-additional-client.sh.
set -euo pipefail

# Provisioned output lands under <WORKSPACE_PARENT_DIR>/net-<NET_NUMBER>/. Default
# to deploy/workspace so it matches the compose mounts, README, and .gitignore.
PROJECT_YAML="${1:?Error: PROJECT_YAML is required}"
NET_NUMBER="${2:?Error: NET_NUMBER is required}"
WORKSPACE_PARENT_DIR="${3:-deploy/workspace}"

# Other configurations
VERBOSE="true"

log() { echo "$*"; }
vlog() { if [[ "${VERBOSE}" == "true" ]]; then echo "   [verbose] $*"; fi }

WORKSPACE_DIR="${WORKSPACE_PARENT_DIR}/net-${NET_NUMBER}"
SERVICES_DIR="${WORKSPACE_DIR}/services"

# Clean up any existing workspace directory for this network
if [[ -d "${WORKSPACE_DIR}" ]]; then
    echo "🧹 Cleaning up existing workspace directory: ${WORKSPACE_DIR}"
    vlog "Removing directory: ${WORKSPACE_DIR}"
    rm -rf "${WORKSPACE_DIR}"
fi

# Run NVFLARE provisioning.
#
# Run nvflare from a uv project that actually declares it. The repo-root `flip`
# project is an umbrella with no dependencies (and no uv workspace), so a bare
# `uv run nvflare` from here resolves nothing — `Failed to spawn: nvflare`. nvflare
# is declared by `fl-services/fl-api-base` (no torch/MONAI, so lighter than
# `flip-utils`) and by `flip-utils`; we target fl-api-base via `--project`. `--project`
# only selects the environment, so cwd — and therefore the relative PROJECT_YAML /
# WORKSPACE_PARENT_DIR paths below — stays at the repo root.
log "Provisioning network ${NET_NUMBER}..."
uv run --project fl-services/fl-api-base nvflare provision -p "${PROJECT_YAML}" -w "${WORKSPACE_PARENT_DIR}"

echo "Restructuring provisioned files in workspace..."

# Find the prod directory created by nvflare provision
# Takes the last one if multiple exist, though this should not be the case since we clean up the workspace above
PROD_DIR=$(find "${WORKSPACE_DIR}" -name "prod_*" -type d | tail -n 1)

if [[ -z "${PROD_DIR}" ]]; then
    echo "Error: Could not find prod directory in ${WORKSPACE_DIR}"
    exit 1
fi

rm -rf "${SERVICES_DIR}"
mkdir -p "${SERVICES_DIR}"

# Function to get the first participant name of a given type from the project YAML
# This avoids hardcoding participant (server, admin) names in the script.
# Selects the first match inside yq instead of piping to `head -n 1`, so it stays
# pipefail-safe: a multi-match YAML can't SIGPIPE yq into a cryptic 141. Emits an
# empty string when no participant of the type exists, so callers guard on `-z`.
get_participant_name_by_type() {
  local project_yaml="$1"
  local participant_type="$2"

  yq -r \
    "[.participants[] | select(.type == \"${participant_type}\")][0].name // \"\"" \
    "$project_yaml"
}

# Like get_participant_name_by_type but returns every matching name (one per
# line), not just the first. Used to restructure all clients regardless of how
# many the project YAML declares — the generator may have expanded it to N (see
# generate-project-yaml.sh).
get_participant_names_by_type() {
  local project_yaml="$1"
  local participant_type="$2"

  yq -r \
    ".participants[] | select(.type == \"${participant_type}\") | .name" \
    "$project_yaml"
}

# Function to restructure a participant's files
restructure_participant() {
    local participant_name="$1"
    local participant_name_dest="$2"
    local is_client="$3"

    local src_path="${PROD_DIR}/${participant_name}"
    local dest_path="${SERVICES_DIR}/${participant_name_dest}"

    # Fail fast if `nvflare provision` did not produce this participant's kit.
    # Without this, the restructure below would silently mkdir an empty dest and
    # report success, masking a malformed project YAML or a partial provision.
    if [[ ! -d "${src_path}" ]]; then
        echo "Error: provisioned participant directory not found: ${src_path}" >&2
        exit 1
    fi

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
if [[ -z "${SERVER_NAME}" ]]; then
    echo "Error: ${PROJECT_YAML} declares no server participant" >&2
    exit 1
fi
echo "Identified server participant name: ${SERVER_NAME}"

ADMIN_NAME="$(get_participant_name_by_type "$PROJECT_YAML" admin)"
if [[ -z "${ADMIN_NAME}" ]]; then
    echo "Error: ${PROJECT_YAML} declares no admin participant" >&2
    exit 1
fi
echo "Identified admin participant name: ${ADMIN_NAME}"

# Restructure every client participant. The count is driven entirely by the
# project YAML (dev declares 2; stag/prod are expanded to N by
# generate-project-yaml.sh), so nothing here is hardcoded to a client count.
client_count=0
while IFS= read -r client_name; do
    [[ -z "${client_name}" ]] && continue
    restructure_participant "${client_name}" "${client_name}" 1
    client_count=$((client_count + 1))
done < <(get_participant_names_by_type "$PROJECT_YAML" client)
if [[ "${client_count}" -eq 0 ]]; then
    echo "Error: ${PROJECT_YAML} declares no client participants to restructure" >&2
    exit 1
fi
echo "Restructured ${client_count} client participant(s)"

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
