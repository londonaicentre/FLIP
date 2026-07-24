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

# Ensure N more *claimable* FL kit slots exist on a running stag/prod NVFLARE
# deployment, end to end. N is a target ("N more live slots"), not a mint count:
#
#   1. discover the deployment's nets from S3
#   2. ACTIVATE spares first: kits already minted on every net but absent from
#      FL_KIT_SLOT_NAMES just need listing — no certs, no upload. Up to N are taken,
#      lowest-numbered first (matching the hub's slot_number-ASC claim order).
#   3. MINT only the shortfall M = N - activated on EVERY net: restore +
#      fingerprint-verify each net's CA workspace (state/ holds the root CA),
#      `nvflare provision --add_client` (via make), upload ONLY the new kits to S3
#      (additive `aws s3 cp`; never `sync --delete`) + refresh each net's mirrored
#      state/cert.json CA registry. Skipped entirely when spares cover N (M = 0), so
#      pure activation needs no CA workspace and no provisioning toolchain at all.
#   4. append the activated + minted names to FL_KIT_SLOT_NAMES in the env file
#
# The caller (`make add-fl-kits`) then applies the /flip/fl_kit_slot_names SSM parameter
# (`make apply-fl-kit-slots`), after which the new slots are claimable on the next
# trust registration — flip-api reconciles its pool on a miss, so no restart and no
# task-definition change is needed.
#
# Invoked by `make -C deploy/providers/AWS add-fl-kits N=<n> PROD=stag|true [YES=1]`,
# which exports the env-file vars this script reads (AICENTRE_BUCKET_NAME,
# FLARE_KIT_DATE, AWS_PROFILE, FL_BACKEND, PROD) plus N / YES / MAIN_ENV_FILE.
#
# Safety invariants (live trusts' kits are sacred):
#   - activates existing spares before minting, so a good kit minted on every net is
#     never stranded and the numbering never skips past it
#   - never writes into an existing S3 kit prefix; the emptiness check fails CLOSED
#     (an S3 error aborts the run, it is never read as "empty")
#   - never uses `upload-kits-to-s3` (`aws s3 sync --delete` over the whole net)
#   - aborts if the local root CA fingerprint differs from the S3 server kit's
#     (the kit's rootCA.pem stands in for the CA in state/cert.json — normal
#     provisioning always writes them together)
#   - a mid-run failure prints exactly what was uploaded and how to recover;
#     re-running add-fl-kits is NOT the recovery (it would mint higher numbers
#     and orphan the already-uploaded names)

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
KEY_PREFIX="fl-flare-participant-kits/${FLARE_KIT_DATE}"
BASE_S3="s3://${AICENTRE_BUCKET_NAME}/${KEY_PREFIX}"

log() { echo "🧩 $*"; }

# A mid-run abort must tell the operator what already reached S3 and how to
# recover — the instinctive fix (re-running add-fl-kits) is the WRONG one: new
# names are computed from what exists in S3, so a re-run mints higher numbers
# and permanently orphans the ones uploaded here. The ERR trap only covers
# command failures; guard aborts must go through die(), which routes their
# message through the same recovery reporting (an explicit `exit 1` bypasses
# the trap, which would lose the guidance on exactly the partial-run paths).
UPLOADED=()
on_error() {
    echo "" >&2
    echo "❌ add-fl-kits aborted." >&2
    if [[ ${#UPLOADED[@]} -gt 0 ]]; then
        echo "   Kits already uploaded this run: ${UPLOADED[*]}" >&2
        echo "   Do NOT re-run add-fl-kits to recover — it would mint HIGHER numbers and orphan the" >&2
        echo "   names above. Instead: append the uploaded names to FL_KIT_SLOT_NAMES in" >&2
        echo "   ${MAIN_ENV_FILE} and run 'make apply-fl-kit-slots PROD=${PROD}' (a name uploaded to" >&2
        echo "   only SOME nets must first be minted on the missing nets — see the nvflare README)." >&2
    else
        echo "   Nothing was minted or uploaded. If this failed during the env-file edit (step 5)," >&2
        echo "   check FL_KIT_SLOT_NAMES in ${MAIN_ENV_FILE} is intact, then re-run once the" >&2
        echo "   underlying error is fixed — a pure activation is safe to re-run." >&2
    fi
}
trap on_error ERR

die() {
    printf '%s\n' "$@" >&2
    on_error
    exit 1
}

# --- 0. Fail early on a bad env file (before anything is minted or uploaded) ---

env_names="$(python3 -c '
import json, re, sys
for line in open(sys.argv[1]):
    m = re.match(r"^FL_KIT_SLOT_NAMES=(.*)$", line.strip())
    if m:
        print("\n".join(json.loads(m.group(1))))
        break
else:
    sys.exit(f"FL_KIT_SLOT_NAMES not found in {sys.argv[1]}")
' "${MAIN_ENV_FILE}")" || {
    echo "❌ FL_KIT_SLOT_NAMES is missing or not valid JSON in ${MAIN_ENV_FILE} — fix it before" >&2
    echo "   running add-fl-kits (step 5 appends the new names to that line)." >&2
    exit 1
}

# --- 1. Discover nets + compute the new slot names -----------------------------

nets_listing="$(aws s3 ls "${BASE_S3}/")" || {
    echo "❌ Could not list ${BASE_S3}/ — AWS error above (credentials/profile?), not a config issue." >&2
    exit 1
}
mapfile -t NETS < <(printf '%s\n' "${nets_listing}" | awk '{print $2}' | grep -oE '^net-[0-9]+' | sort -V)
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
s3_names=()  # one entry per (net, name) — the repeat count is what identifies a kit minted on EVERY net
for net in "${NETS[@]}"; do
    services_listing="$(aws s3 ls "${BASE_S3}/${net}/services/")" || {
        echo "❌ Could not list ${BASE_S3}/${net}/services/ — refusing to compute slot numbers" >&2
        echo "   from an incomplete view (a missed name here could reuse a live trust's slot)." >&2
        exit 1
    }
    while IFS= read -r name; do
        [[ -n "${name}" ]] && existing_names+=("${name}") && s3_names+=("${name}")
    done < <(printf '%s\n' "${services_listing}" | awk '{print $2}' | grep -oE '^Trust_[^/]+' || true)
done
while IFS= read -r name; do
    [[ -n "${name}" ]] && existing_names+=("${name}")
done <<<"${env_names}"

# Spare kits: minted on EVERY net yet absent from FL_KIT_SLOT_NAMES. Activating one
# is an env-file edit + `apply-fl-kit-slots` — no new certs, no upload, no restart —
# so we activate spares before minting: a good kit is never stranded and the numbering
# never skips past it. Sorted lowest-number-first (`sort -t_ -k2 -n`) to drain idle
# slots in the same order the hub claims them (slot_number ASC).
# Counted per net (not a plain union): a kit missing entirely from a net (count < nets)
# is excluded here, and a kit present-but-incomplete on a net (an interrupted final-net
# upload still leaves a prefix, so it counts) is caught by the completeness check before
# activation below. Excluded names still bump max_num below, so they are never reused.
spare_names=()
if [[ "${#s3_names[@]}" -gt 0 ]]; then
    while IFS= read -r name; do
        [[ -n "${name}" ]] && spare_names+=("${name}")
    done < <(comm -23 \
        <(printf '%s\n' "${s3_names[@]}" | grep -E '^Trust_[0-9]+$' | sort | uniq -c |
            awk -v nets="${#NETS[@]}" '$1 == nets { print $2 }' | sort -u) \
        <(printf '%s\n' "${env_names}" | grep -E '^Trust_[0-9]+$' | sort -u) |
        sort -t_ -k2 -n)
fi

# Split the request of N more live slots into ACTIVATE (reuse spares, cheapest) then
# MINT (the shortfall). N is a target, not a mint count: with enough spares, mint_count
# is 0 and the whole CA-restore/mint path below is skipped.
ACTIVATE_NAMES=()
if [[ "${#spare_names[@]}" -gt 0 ]]; then
    for name in "${spare_names[@]}"; do
        [[ "${#ACTIVATE_NAMES[@]}" -ge "${N}" ]] && break
        ACTIVATE_NAMES+=("${name}")
    done
fi
mint_count=$((N - ${#ACTIVATE_NAMES[@]}))

max_num=0
for name in "${existing_names[@]}"; do
    # 10# forces base 10: bash arithmetic reads a leading-zero numeral as octal,
    # so a zero-padded suffix (Trust_008) would crash with "value too great for
    # base" and Trust_010–Trust_017 would silently compute the wrong number.
    # flip-api's _slot_number parses the same names as decimal, so such names
    # do reach this script. max_num is stored normalized (no leading zeros),
    # keeping the mint loop's $((max_num + i)) safe.
    if [[ "${name}" =~ ^Trust_([0-9]+)$ ]] && (( 10#${BASH_REMATCH[1]} > max_num )); then
        max_num=$((10#${BASH_REMATCH[1]}))
    fi
done

MINT_NAMES=()
for ((i = 1; i <= mint_count; i++)); do
    MINT_NAMES+=("Trust_$((max_num + i))")
done

# Everything that lands in FL_KIT_SLOT_NAMES: activated spares + freshly minted (always
# exactly N names, since mint_count = N - #activated). Built with guards so an empty
# component array is never expanded under `set -u`.
APPEND_NAMES=()
[[ "${#ACTIVATE_NAMES[@]}" -gt 0 ]] && APPEND_NAMES+=("${ACTIVATE_NAMES[@]}")
[[ "${#MINT_NAMES[@]}" -gt 0 ]] && APPEND_NAMES+=("${MINT_NAMES[@]}")

# A spare qualifies for activation only if its kit is COMPLETE on every net. The per-net
# count above only proves a prefix exists, which a kit whose final net's recursive
# `aws s3 cp` upload was interrupted still satisfies (≥1 object under services/<name>/).
# Activating such a partial kit would silently hand a trust a slot it cannot join on the
# short net, so verify the essential startup files exist on every net and fail closed on
# a gap — a partial kit is an interrupted-run anomaly to clean up (see the mid-run
# recovery in fl-services/nvflare/README.md), not something to activate. Only the ≤N
# names we are about to activate are checked, not every spare.
if [[ "${#ACTIVATE_NAMES[@]}" -gt 0 ]]; then
    for name in "${ACTIVATE_NAMES[@]}"; do
        for net in "${NETS[@]}"; do
            startup_keys="$(aws s3api list-objects-v2 --bucket "${AICENTRE_BUCKET_NAME}" \
                --prefix "${KEY_PREFIX}/${net}/services/${name}/startup/" \
                --query 'Contents[].Key' --output text)" || {
                echo "❌ could not verify ${name}/ on ${net} — refusing to activate a spare blind." >&2
                exit 1
            }
            for required in client.crt client.key rootCA.pem fed_client.json sub_start.sh; do
                if [[ "${startup_keys}" != *"/startup/${required}"* ]]; then
                    echo "❌ spare ${name} is incomplete on ${net}: missing startup/${required}." >&2
                    echo "   A partially-uploaded kit (an interrupted prior add-fl-kits run) must be cleaned" >&2
                    echo "   up before it can be activated — see the mid-run recovery in" >&2
                    echo "   fl-services/nvflare/README.md. Refusing to activate an incomplete kit." >&2
                    exit 1
                fi
            done
        done
    done
fi

activate_display="(none — no spare kits)"
[[ "${#ACTIVATE_NAMES[@]}" -gt 0 ]] && activate_display="${ACTIVATE_NAMES[*]}"
mint_display="(none — spares cover N)"
[[ "${#MINT_NAMES[@]}" -gt 0 ]] && mint_display="${MINT_NAMES[*]}"
# Show the "existing max" hint only when minting; noise otherwise.
mint_line="${mint_display}"
[[ "${#MINT_NAMES[@]}" -gt 0 ]] && mint_line="${mint_display}  (existing max: Trust_${max_num})"

echo ""
echo "   Plan (${ENV_NAME}):  ${N} more live slot(s)"
echo "     bucket        ${AICENTRE_BUCKET_NAME} (kit date ${FLARE_KIT_DATE})"
echo "     nets          ${NETS[*]}"
echo "     activate      ${activate_display}"
echo "     mint          ${mint_line}"
echo "     env file      ${MAIN_ENV_FILE}"
if [[ "${#ACTIVATE_NAMES[@]}" -gt 0 ]]; then
    # Spare detection sees S3 and the env file, not the hub's fl_kit_slot table (rows are
    # never deleted). A previously-activated name that later dropped out of the env file
    # would be "activated" again here yet add zero claimable capacity.
    echo "     ⚠️  activation assumes ${MAIN_ENV_FILE} still lists every previously-activated"
    echo "        name — see 'Activating the new slot on the hub' in fl-services/nvflare/README.md."
fi
echo ""
if [[ "${YES}" != "1" ]]; then
    read -r -p "   Proceed? [y/N] " answer
    [[ "${answer}" == "y" || "${answer}" == "Y" ]] || { echo "Aborted."; exit 1; }
fi

# --- 2-4. MINT the shortfall: per net restore/verify CA workspace, mint, upload -----
# Skipped entirely when spares already cover N (mint_count == 0) — pure activation
# touches no CA state and needs none of the provisioning toolchain.

if [[ "${#MINT_NAMES[@]}" -gt 0 ]]; then
for net in "${NETS[@]}"; do
    net_number="${net#net-}"
    workspace="${WORKSPACE_PARENT}/${net}"
    server_kit="services/fl-server-${net}"

    # Restore the CA state + server kit from the S3 mirror if this checkout has
    # never provisioned (or added to) this net — the two things add-client.sh needs.
    if [[ ! -f "${workspace}/state/cert.json" ]]; then
        log "${net}: no local CA state — restoring workspace from ${BASE_S3}/${net}/"
        aws s3 cp "${BASE_S3}/${net}/state/" "${workspace}/state/" --recursive --only-show-errors
        if [[ ! -f "${workspace}/state/cert.json" ]]; then
            die "❌ ${net}: the S3 mirror has no state/cert.json — this net's CA state was never" \
                "   uploaded, so kits can only be minted from the checkout that provisioned it." \
                "   (Do NOT re-provision: that rotates the CA and forces a fleet-wide redeploy.)"
        fi
    fi
    if [[ ! -f "${workspace}/${server_kit}/startup/rootCA.pem" ]]; then
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
        die "❌ ${net}: local root CA does not match the live S3 kits — refusing to mint." \
            "   local: ${local_fp}" \
            "   s3:    ${s3_fp}" \
            "   The workspace at ${workspace} is not the one that provisioned this net."
    fi
    rm -f "${s3_root_ca}"

    for name in "${MINT_NAMES[@]}"; do
        # Refuse to touch a non-empty S3 prefix — an existing kit may belong to a
        # live trust; add-client.sh separately refuses existing local kits. This
        # check fails CLOSED: KeyCount comes from s3api (non-zero exit on any AWS
        # error aborts the run), never from empty output of a failed listing.
        key_count="$(aws s3api list-objects-v2 --bucket "${AICENTRE_BUCKET_NAME}" \
            --prefix "${KEY_PREFIX}/${net}/services/${name}/" --max-keys 1 \
            --query 'KeyCount' --output text)" ||
            die "❌ ${net}: could not check whether ${name}/ already exists in S3 — refusing to upload blind."
        if [[ "${key_count}" != "0" ]]; then
            die "❌ ${net}: ${BASE_S3}/${net}/services/${name}/ already has objects — refusing to overwrite."
        fi

        log "${net}: minting ${name} against the existing root CA..."
        make -C "${NVFLARE_DIR}" "provision-add-client-${ENV_NAME}" NET_NUMBER="${net_number}" CLIENT_NAME="${name}"

        log "${net}: uploading ${name} (additive)..."
        aws s3 cp "${workspace}/services/${name}/" "${BASE_S3}/${net}/services/${name}/" \
            --recursive --only-show-errors
        UPLOADED+=("${net}/${name}")
    done

    # Refresh the mirrored CA registry so the next add from a fresh checkout signs
    # with a registry that knows these identities.
    aws s3 cp "${workspace}/state/cert.json" "${BASE_S3}/${net}/state/cert.json" --only-show-errors
    log "${net}: done (${#MINT_NAMES[@]} kits minted + uploaded, state/cert.json refreshed)."
done
fi

# --- 5. Append the activated + minted names to FL_KIT_SLOT_NAMES in the env file ---

python3 - "${MAIN_ENV_FILE}" "${APPEND_NAMES[@]}" <<'PYEOF'
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
log "Slots ensured: activated [${activate_display}], minted [${mint_display}]."
log "Next: the caller applies the SSM parameter (make apply-fl-kit-slots) so the new slots"
log "become claimable on the next trust registration — no restart needed."
