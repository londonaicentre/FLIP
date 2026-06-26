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

# Generate an NVFLARE project YAML with N client participants from a base file.
#
# The recommended NVFLARE pattern is to provision more participant kits than you
# currently need, so spare slots are always available: re-provisioning to add a
# client regenerates every existing participant's certs (and can rotate the root
# CA), forcing a redeploy to every already-onboarded trust. Over-provisioning
# avoids that — see FLIP#626.
#
# The base project YAML declares the server, admin, builders and a single
# canonical client participant (the template). This script clones that template
# into Trust_1 .. Trust_N, preserving the template's env-specific fields
# (connect_to, enable_byoc, resource, ...) and substituting only the name. The
# expanded project YAML is written to a (gitignored) output path that
# provision-network.sh then feeds to `nvflare provision`.
#
# Usage: ./generate-project-yaml.sh <base_project_yaml> <num_clients> <out_yaml>

set -euo pipefail

BASE_YAML="${1:?Error: base_project_yaml is required}"
NUM_CLIENTS="${2:?Error: num_clients is required}"
OUT_YAML="${3:?Error: out_yaml is required}"

if ! [[ "${NUM_CLIENTS}" =~ ^[1-9][0-9]*$ ]]; then
    echo "Error: num_clients must be a positive integer (got '${NUM_CLIENTS}')" >&2
    exit 1
fi

if [[ ! -f "${BASE_YAML}" ]]; then
    echo "Error: base project YAML not found: ${BASE_YAML}" >&2
    exit 1
fi

# Extract the first client participant as the template, dropping its name (we
# assign Trust_<i> per clone). The JSON round-trip strips the template's inline
# comments so they are not replicated N times in the generated file. Fail
# clearly if the base declares no client.
TEMPLATE="$(yq -o=json '[.participants[] | select(.type == "client")][0] | del(.name)' "${BASE_YAML}" | yq -P '.' -)"
if [[ -z "${TEMPLATE}" || "${TEMPLATE}" == "null" ]]; then
    echo "Error: ${BASE_YAML} declares no client participant to use as a template" >&2
    exit 1
fi

# Build the expanded client sequence (Trust_1 .. Trust_N) as YAML text: a
# sequence entry per client, with the comment-free template indented two spaces
# beneath its name.
CLIENTS=""
for ((i = 1; i <= NUM_CLIENTS; i++)); do
    CLIENTS+="- name: Trust_${i}"$'\n'
    CLIENTS+="$(printf '%s\n' "${TEMPLATE}" | sed 's/^/  /')"$'\n'
done
# CLIENTS is read by yq below via strenv(), so it must live in the process
# environment. The environment block counts against the OS ARG_MAX ceiling
# (~2 MB on Linux), which bounds NUM_CLIENTS: at N=500 this is ~5K lines
# (~100 KB), comfortably under it. Pushing into the tens of thousands could
# exceed ARG_MAX and fail — write CLIENTS to a temp file and pipe it into yq
# instead if you ever need counts that large.
export CLIENTS

mkdir -p "$(dirname "${OUT_YAML}")"

# Rebuild .participants as server -> generated clients -> everything else
# (admin, builders, ...), so the base's server-then-clients-then-admin ordering
# is preserved rather than leaving the clients dangling after admin. The
# server/admin/builders entries (and their one-off comments) pass through
# unchanged in content; only the client template is swapped for the expansion.
yq '
  .participants = (
    [.participants[] | select(.type == "server")]
    + (strenv(CLIENTS) | from_yaml)
    + [.participants[] | select(.type != "server" and .type != "client")]
  )
' "${BASE_YAML}" > "${OUT_YAML}"

echo "Generated ${OUT_YAML} with ${NUM_CLIENTS} client participant(s) (Trust_1 .. Trust_${NUM_CLIENTS})"
