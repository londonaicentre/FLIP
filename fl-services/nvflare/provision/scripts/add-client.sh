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

# Add ONE client to an already-provisioned NVFLARE network — zero disruption.
#
# Usage: ./fl-services/nvflare/provision/scripts/add-client.sh \
#          <net_number> <new_client_name> [project_yaml] [workspace_parent_dir]
# Normally invoked via `make -C fl-services/nvflare provision-add-client` (cwd = fl-services/nvflare).
#
# Unlike provision-network.sh, this does NOT wipe the workspace. It runs the official
# `nvflare provision … --add_client <yaml>` flag against the EXISTING workspace, so
# NVFLARE's CertBuilder loads the network's root CA from the preserved state/ and
# signs only the new client's kit with it. Every already-onboarded participant's
# files are left byte-identical (no cert churn, no fleet-wide redeploy) — contrast a
# full re-provision, which wipes state/ and rotates the CA. This is the escape hatch
# for when the over-provisioned slot pool (FLIP#626) runs dry, or for ad-hoc dev adds.

# Fail fast and loud (see provision-network.sh for the rationale).
set -euo pipefail

NET_NUMBER="${1:?Error: NET_NUMBER is required}"
NEW_CLIENT_NAME="${2:?Error: NEW_CLIENT_NAME is required (e.g. Trust_3)}"
PROJECT_YAML="${3:-provision/net-${NET_NUMBER}_project_dev.yml}"
WORKSPACE_PARENT_DIR="${4:-provision/workspace-dev}"

VERBOSE="true"

# Shared restructure helpers (log/vlog, get_participant_name_by_type,
# restructure_participant, …) — sourced by absolute path so cwd doesn't matter.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=restructure-lib.sh
source "${SCRIPT_DIR}/restructure-lib.sh"

WORKSPACE_DIR="${WORKSPACE_PARENT_DIR}/net-${NET_NUMBER}"
SERVICES_DIR="${WORKSPACE_DIR}/services"
SERVER_DEST="fl-server-net-${NET_NUMBER}"

# --- Preconditions ------------------------------------------------------------

if [[ ! -f "${PROJECT_YAML}" ]]; then
    echo "Error: project YAML not found: ${PROJECT_YAML}" >&2
    exit 1
fi

# The network must already be provisioned with its state/ preserved — that is what
# holds the root CA the new client's cert is signed against. provision-network.sh
# leaves state/ in place (it only deletes the prod_NN snapshot), so a prior
# `make provision`/`provision-prod` is the prerequisite.
if [[ ! -f "${WORKSPACE_DIR}/state/cert.json" ]]; then
    echo "Error: ${WORKSPACE_DIR}/state/cert.json not found — net-${NET_NUMBER} is not provisioned." >&2
    echo "       Provision it first (e.g. make -C fl-services/nvflare provision NET_NUMBER=${NET_NUMBER})." >&2
    exit 1
fi

if [[ ! -f "${SERVICES_DIR}/${SERVER_DEST}/startup/rootCA.pem" ]]; then
    echo "Error: server kit not found at ${SERVICES_DIR}/${SERVER_DEST}/ — net-${NET_NUMBER} is not fully provisioned." >&2
    exit 1
fi

# Refuse to clobber an existing client kit.
if [[ -e "${SERVICES_DIR}/${NEW_CLIENT_NAME}" ]]; then
    echo "Error: client '${NEW_CLIENT_NAME}' already exists at ${SERVICES_DIR}/${NEW_CLIENT_NAME} — nothing to do." >&2
    exit 1
fi

# Refuse if the name already names a participant in the project YAML: --add_client
# would then create a duplicate participant. (For dev that means Trust_1/Trust_2;
# for the stag/prod base template it means the single Trust_1 template.)
if yq -e "[.participants[] | select(.name == \"${NEW_CLIENT_NAME}\")] | length > 0" "${PROJECT_YAML}" >/dev/null 2>&1; then
    echo "Error: '${NEW_CLIENT_NAME}' is already a participant in ${PROJECT_YAML}; pick a name not declared there." >&2
    exit 1
fi

# --- Build the add_client YAML ------------------------------------------------

# Clone the project's client template (first client participant), drop its name,
# and assign the new one. The JSON round-trip strips the template's inline comments
# (same approach as generate-project-yaml.sh). `type` is preserved but NVFLARE
# forces it to "client" regardless. The participant block carries the env-specific
# fields (connect_to, enable_byoc, resource, …) so the new client matches the rest.
CLIENT_COUNT="$(yq '[.participants[] | select(.type == "client")] | length' "${PROJECT_YAML}")"
if [[ "${CLIENT_COUNT}" -lt 1 ]]; then
    echo "Error: ${PROJECT_YAML} declares no client participant to use as a template." >&2
    exit 1
fi
TEMPLATE="$(yq -o=json '[.participants[] | select(.type == "client")][0] | del(.name)' "${PROJECT_YAML}" | yq -P '.' -)"

ADD_CLIENT_YAML="$(mktemp -t add_client.XXXXXX.yml)"
trap 'rm -f "${ADD_CLIENT_YAML}"' EXIT
{
    printf 'name: %s\n' "${NEW_CLIENT_NAME}"
    printf '%s\n' "${TEMPLATE}"
} > "${ADD_CLIENT_YAML}"

vlog "add_client YAML:"
[[ "${VERBOSE}" == "true" ]] && sed 's/^/   [verbose]   /' "${ADD_CLIENT_YAML}"

# --- Provision the new client against the existing CA -------------------------

# NO workspace wipe: the existing state/ (root CA) must survive so the new client is
# signed by it and every existing participant's cert is reused unchanged. NVFLARE
# writes a fresh prod_NN snapshot (workspace.py increments past existing ones).
#
# --force is required from NVFLARE 2.8.0 on: `provision` now refuses to run against an
# already-populated workspace in non-interactive mode (it would otherwise prompt Y/N to
# continue). --force ONLY skips that confirmation — it does not wipe or regenerate the
# workspace (nvflare/lighter/provision.py just bypasses the prompt branch), so state/ and
# the root CA are preserved exactly as before, which is the whole point of add-client.
log "Adding client '${NEW_CLIENT_NAME}' to net-${NET_NUMBER} via nvflare provision --add_client..."
uv run --project fl-api-base nvflare provision \
    -p "${PROJECT_YAML}" -w "${WORKSPACE_PARENT_DIR}" --add_client "${ADD_CLIENT_YAML}" --force

# The just-written snapshot is the highest-numbered prod_NN (workspace.py only ever
# increments), so `sort | tail -n 1` finds it even if a stale prod_* lingers.
PROD_DIR=$(find "${WORKSPACE_DIR}" -maxdepth 1 -name "prod_*" -type d | sort | tail -n 1)
if [[ -z "${PROD_DIR}" ]]; then
    echo "Error: no prod_* directory produced under ${WORKSPACE_DIR}" >&2
    exit 1
fi
vlog "New prod snapshot: ${PROD_DIR}"

# --- Restructure ONLY the new client ------------------------------------------

# Restructure just the new client into services/. The snapshot also re-emits the
# server/admin/existing clients (all reused from state, byte-identical), but we
# deliberately leave the existing services/ kits untouched and ignore those.
echo "Restructuring new client into ${SERVICES_DIR}/${NEW_CLIENT_NAME}..."
restructure_participant "${NEW_CLIENT_NAME}" "${NEW_CLIENT_NAME}" 1

# Sanity check: the new client must be on the network's existing root CA.
NEW_CA="${SERVICES_DIR}/${NEW_CLIENT_NAME}/startup/rootCA.pem"
SERVER_CA="${SERVICES_DIR}/${SERVER_DEST}/startup/rootCA.pem"
if ! cmp -s "${SERVER_CA}" "${NEW_CA}"; then
    echo "Error: ${NEW_CLIENT_NAME}'s rootCA.pem does not match the server's — CA mismatch, aborting." >&2
    exit 1
fi
vlog "rootCA.pem matches the server — new client is on the existing CA."

# --- Clean up -----------------------------------------------------------------

echo "Cleaning up prod snapshot..."
vlog "Removing directory: ${PROD_DIR}"
rm -rf "${PROD_DIR}"

echo ""
echo "Client '${NEW_CLIENT_NAME}' added to net-${NET_NUMBER} successfully"
echo "  Kit: ${SERVICES_DIR}/${NEW_CLIENT_NAME}/ (gitignored), signed by the existing root CA"
echo "  Existing participants were left untouched (no re-provision, no cert churn)."
