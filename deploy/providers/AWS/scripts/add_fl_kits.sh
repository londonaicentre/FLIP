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

# Add N new FL kit slots to a running stag/prod NVFLARE deployment, end to end:
#
#   1. discover the deployment's nets from S3 and compute the next Trust_<n> names
#   2. restore + fingerprint-verify each net's CA workspace (state/ holds the root CA)
#   3. mint each new name on EVERY net (`nvflare provision --add_client` via make)
#   4. upload ONLY the new kits to S3 (additive `aws s3 cp`; never `sync --delete`)
#   5. append the names to FL_KIT_SLOT_NAMES in the env file
#
# The caller (`make add-fl-kits`) then applies the FLIP_API secret
# (`make apply-flip-secret`), after which the new slots are claimable on the next
# trust registration — flip-api reconciles its pool on a miss, so no restart and no
# task-definition change is needed.
#
# Invoked by `make -C deploy/providers/AWS add-fl-kits N=<n> PROD=stag|true [YES=1]`,
# which exports the env-file vars this script reads (AICENTRE_BUCKET_NAME,
# FLARE_KIT_DATE, AWS_PROFILE, FL_BACKEND, PROD) plus N / YES / MAIN_ENV_FILE.
#
# Safety invariants (live trusts' kits are sacred):
#   - never writes into an existing S3 kit prefix; refuses if the target is non-empty
#   - never uses `upload-kits-to-s3` (`aws s3 sync --delete` over the whole net)
#   - aborts if the local root CA fingerprint differs from the S3 server kit's

set -euo pipefail

N="${N:?N is required (number of kit slots to add, e.g. N=2)}"
PROD="${PROD:?PROD is required (stag|true)}"
AICENTRE_BUCKET_NAME="${AICENTRE_BUCKET_NAME:?AICENTRE_BUCKET_NAME must be set (run via make with PROD=stag|true)}"
FLARE_KIT_DATE="${FLARE_KIT_DATE:?FLARE_KIT_DATE must be set (run via make with PROD=stag|true)}"
MAIN_ENV_FILE="${MAIN_ENV_FILE:?MAIN_ENV_FILE must be set (the env file to append FL_KIT_SLOT_NAMES to)}"
YES="${YES:-}"

if [[ "${FL_BACKEND:-nvflare}" != "nvflare" ]]; then
    echo "❌ add-fl-kits supports FL_BACKEND=nvflare only. Flower kits are per-supernode key" >&2
    echo "   pairs whose net-side labelling happens at net startup — see fl-services/flower/." >&2
    exit 1
fi

if ! [[ "${N}" =~ ^[0-9]+$ ]] || [[ "${N}" -lt 1 ]]; then
    echo "❌ N must be a positive integer, got '${N}'." >&2
    exit 1
fi

case "${PROD}" in
true) ENV_NAME="prod" ;;
stag) ENV_NAME="stag" ;;
*)
    echo "❌ PROD must be 'stag' or 'true', got '${PROD}'." >&2
    exit 1
    ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
NVFLARE_DIR="${REPO_ROOT}/fl-services/nvflare"
WORKSPACE_PARENT="${NVFLARE_DIR}/provision/workspace-${ENV_NAME}"
BASE_S3="s3://${AICENTRE_BUCKET_NAME}/fl-flare-participant-kits/${FLARE_KIT_DATE}"

log() { echo "🧩 $*"; }

# --- 1. Discover nets + compute the new slot names -----------------------------

mapfile -t NETS < <(aws s3 ls "${BASE_S3}/" | awk '{print $2}' | grep -oE '^net-[0-9]+' | sort -V)
if [[ "${#NETS[@]}" -eq 0 ]]; then
    echo "❌ No net-* prefixes under ${BASE_S3}/ — is FLARE_KIT_DATE correct?" >&2
    exit 1
fi
log "Nets in S3 (${FLARE_KIT_DATE}): ${NETS[*]}"

# Existing slot names: the union of every net's services/Trust_* prefixes and the
# env file's FL_KIT_SLOT_NAMES. The max trailing number across the union decides
# where the new names start, so a name is never reused even if a kit upload and an
# env-file entry have drifted apart.
existing_names=()
for net in "${NETS[@]}"; do
    while IFS= read -r name; do
        existing_names+=("${name}")
    done < <(aws s3 ls "${BASE_S3}/${net}/services/" | awk '{print $2}' | grep -oE '^Trust_[^/]+' || true)
done
while IFS= read -r name; do
    existing_names+=("${name}")
done < <(python3 -c '
import json, re, sys
for line in open(sys.argv[1]):
    m = re.match(r"^FL_KIT_SLOT_NAMES=(.*)$", line.strip())
    if m:
        print("\n".join(json.loads(m.group(1))))
' "${MAIN_ENV_FILE}")

max_num=0
for name in "${existing_names[@]}"; do
    if [[ "${name}" =~ ^Trust_([0-9]+)$ ]] && [[ "${BASH_REMATCH[1]}" -gt "${max_num}" ]]; then
        max_num="${BASH_REMATCH[1]}"
    fi
done

NEW_NAMES=()
for ((i = 1; i <= N; i++)); do
    NEW_NAMES+=("Trust_$((max_num + i))")
done

echo ""
echo "   Plan (${ENV_NAME}):"
echo "     bucket        ${AICENTRE_BUCKET_NAME} (kit date ${FLARE_KIT_DATE})"
echo "     nets          ${NETS[*]}"
echo "     new slots     ${NEW_NAMES[*]}  (existing max: Trust_${max_num})"
echo "     env file      ${MAIN_ENV_FILE}"
echo ""
if [[ "${YES}" != "1" ]]; then
    read -r -p "   Proceed? [y/N] " answer
    [[ "${answer}" == "y" || "${answer}" == "Y" ]] || { echo "Aborted."; exit 1; }
fi

# --- 2-4. Per net: restore/verify CA workspace, mint, upload additively --------

for net in "${NETS[@]}"; do
    net_number="${net#net-}"
    workspace="${WORKSPACE_PARENT}/${net}"
    server_kit="services/fl-server-${net}"

    # Restore the CA state + server kit from the S3 mirror if this checkout has
    # never provisioned (or added to) this net — the two things add-client.sh needs.
    if [[ ! -f "${workspace}/state/cert.json" ]]; then
        log "${net}: no local CA state — restoring workspace from ${BASE_S3}/${net}/"
        aws s3 cp "${BASE_S3}/${net}/state/" "${workspace}/state/" --recursive --only-show-errors
        aws s3 cp "${BASE_S3}/${net}/${server_kit}/" "${workspace}/${server_kit}/" --recursive --only-show-errors
    fi

    # The local CA must be the one that signed the live kits: compare root-CA
    # fingerprints (bucket ETags are SSE-KMS, not MD5s, so hash the cert itself).
    s3_root_ca="$(mktemp -t rootCA.XXXXXX.pem)"
    trap 'rm -f "${s3_root_ca}"' EXIT
    aws s3 cp "${BASE_S3}/${net}/${server_kit}/startup/rootCA.pem" "${s3_root_ca}" --only-show-errors
    local_fp="$(openssl x509 -in "${workspace}/${server_kit}/startup/rootCA.pem" -noout -fingerprint -sha256)"
    s3_fp="$(openssl x509 -in "${s3_root_ca}" -noout -fingerprint -sha256)"
    if [[ "${local_fp}" != "${s3_fp}" ]]; then
        echo "❌ ${net}: local root CA does not match the live S3 kits — refusing to mint." >&2
        echo "   local: ${local_fp}" >&2
        echo "   s3:    ${s3_fp}" >&2
        echo "   The workspace at ${workspace} is not the one that provisioned this net." >&2
        exit 1
    fi
    rm -f "${s3_root_ca}"

    for name in "${NEW_NAMES[@]}"; do
        # Refuse to touch a non-empty S3 prefix — an existing kit may belong to a
        # live trust; add-client.sh separately refuses existing local kits.
        if [[ -n "$(aws s3 ls "${BASE_S3}/${net}/services/${name}/" 2>/dev/null)" ]]; then
            echo "❌ ${net}: ${BASE_S3}/${net}/services/${name}/ already has objects — refusing to overwrite." >&2
            exit 1
        fi

        log "${net}: minting ${name} against the existing root CA..."
        make -C "${NVFLARE_DIR}" "provision-add-client-${ENV_NAME}" NET_NUMBER="${net_number}" CLIENT_NAME="${name}"

        log "${net}: uploading ${name} (additive)..."
        aws s3 cp "${workspace}/services/${name}/" "${BASE_S3}/${net}/services/${name}/" \
            --recursive --only-show-errors
    done

    # Refresh the mirrored CA registry so the next add from a fresh checkout signs
    # with a registry that knows these identities.
    aws s3 cp "${workspace}/state/cert.json" "${BASE_S3}/${net}/state/cert.json" --only-show-errors
    log "${net}: done (${#NEW_NAMES[@]} kits minted + uploaded, state/cert.json refreshed)."
done

# --- 5. Append the new names to FL_KIT_SLOT_NAMES in the env file --------------

python3 - "${MAIN_ENV_FILE}" "${NEW_NAMES[@]}" <<'PYEOF'
import json
import re
import sys

path, new_names = sys.argv[1], sys.argv[2:]
with open(path) as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    m = re.match(r"^FL_KIT_SLOT_NAMES=(.*)$", line.strip())
    if m:
        names = json.loads(m.group(1))
        names += [n for n in new_names if n not in names]
        lines[i] = f"FL_KIT_SLOT_NAMES={json.dumps(names)}\n"
        break
else:
    sys.exit(f"FL_KIT_SLOT_NAMES not found in {path} — add it before running add-fl-kits.")

with open(path, "w") as f:
    f.writelines(lines)
print(f"   FL_KIT_SLOT_NAMES in {path} now: {json.dumps(names)}")
PYEOF

echo ""
log "Kits minted + uploaded. Next: the caller applies the FLIP_API secret (make apply-flip-secret)"
log "so the new slots become claimable on the next trust registration — no restart needed."
