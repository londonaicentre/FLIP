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
# Usage: ./fl-services/nvflare/provision/scripts/provision-network.sh <project_yaml_file> <net_number> [workspace_parent_dir]
# Normally invoked via `make -C fl-services/nvflare provision` (cwd = fl-services/nvflare).

# Fail fast and loud: abort on any command error (-e), on an unset variable (-u),
# and on a failure anywhere in a pipeline (-o pipefail). Without this a non-zero
# `nvflare provision` (or `yq`) could leave partial output yet let the script
# carry on restructuring and report success.
set -euo pipefail

# Provisioned output lands under <WORKSPACE_PARENT_DIR>/net-<NET_NUMBER>/. Default to
# provision/workspace-dev (beside this script's provision/ home under fl-services/nvflare/)
# so it matches the compose mounts, README, and .gitignore.
PROJECT_YAML="${1:?Error: PROJECT_YAML is required}"
NET_NUMBER="${2:?Error: NET_NUMBER is required}"
WORKSPACE_PARENT_DIR="${3:-provision/workspace-dev}"

# Other configurations
VERBOSE="true"

# Shared restructure helpers (log/vlog, get_participant_name(s)_by_type,
# restructure_participant, create_*_template), also used by add-client.sh. Sourced
# by absolute path off BASH_SOURCE so it resolves regardless of the caller's cwd.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=restructure-lib.sh
source "${SCRIPT_DIR}/restructure-lib.sh"

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
# Run nvflare from a uv project that actually declares it. A bare `uv run nvflare`
# resolves nothing here — `Failed to spawn: nvflare`. nvflare is declared by the
# colocated `fl-api-base` uv project (no torch/MONAI, so lighter than `flip-utils`),
# targeted via `--project fl-api-base`. `--project` only selects the environment, so
# cwd — and therefore the relative PROJECT_YAML / WORKSPACE_PARENT_DIR paths below —
# stays at fl-services/nvflare/ (where the Makefile invokes this).
log "Provisioning network ${NET_NUMBER}..."
# uv older than 0.10.0 cannot parse `exclude-newer = "3 days"`; it warns, drops
# the cooldown and rewrites uv.lock. Fail here instead of silently re-resolving.
"${SCRIPT_DIR}/../../../../scripts/check-uv-version.sh"
uv run --project fl-api-base nvflare provision -p "${PROJECT_YAML}" -w "${WORKSPACE_PARENT_DIR}"

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
