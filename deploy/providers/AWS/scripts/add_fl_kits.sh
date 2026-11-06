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

# Mint N more *claimable* FL kit slots on a running stag/prod FLARE deployment, end to
# end:
#
#   1. discover the deployment's nets and existing kit names from S3
#   2. on EVERY net: restore + fingerprint-verify the CA workspace (state/ holds the
#      root CA), `nvflare provision --add_client` (via make) the next Trust_<n> names,
#      upload ONLY the new kits (additive `aws s3 cp`; never `sync --delete`) and
#      refresh the net's mirrored state/cert.json CA registry
#
# flip-api's pool is every slot whose kit is complete on every net (FLIP#1720), and it
# re-reads S3 when the pool runs dry, so the slots are claimable at the next trust
# registration — no Terraform, no restart.
#
# Invoked by `make -C deploy/providers/AWS add-fl-kits N=<n> HUB=<stag|prod> [YES=1]`
# (the Makefile hands this script the environment CLASS as ENV_NAME=prod|stag),
# which exports the env-file vars this script reads (AICENTRE_BUCKET_NAME,
# FLARE_KIT_DATE, AWS_PROFILE, FL_BACKEND, HUB) plus N / YES.
#
# Safety invariants (live trusts' kits are sacred):
#   - never writes into an existing S3 kit prefix; the emptiness check fails CLOSED
#     (an S3 error aborts the run, it is never read as "empty")
#   - never uses `upload-kits-to-s3` (`aws s3 sync --delete` over the whole net)
#   - aborts if the local root CA fingerprint differs from the S3 server kit's
#     (the kit's rootCA.pem stands in for the CA in state/cert.json — normal
#     provisioning always writes them together)
#   - uploads a kit's required startup files last, so a half-uploaded kit never counts
#   - a mid-run failure prints exactly what was uploaded and how to recover

set -euo pipefail

N="${N:?N is required (number of kit slots to add, e.g. N=2)}"
AICENTRE_BUCKET_NAME="${AICENTRE_BUCKET_NAME:?AICENTRE_BUCKET_NAME must be set (run via make with HUB=<env>)}"
FLARE_KIT_DATE="${FLARE_KIT_DATE:?FLARE_KIT_DATE must be set (run via make with HUB=<env>)}"
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

# ENV_NAME (prod|stag) selects the CA workspace and project YAML
# (provision/workspace-<env>, net-N_project_<env>.yml). It is the environment
# CLASS the Makefile derives from HUB (deploy/env_mode.mk's ENV_CLASS, the
# same value fl-services/nvflare/Makefile keys FL_KIT_WORKSPACE on), handed in
# rather than re-derived here. Both network modes map onto the same class: an LZA
# estate's kits carry the same server FQDN and root CA as the self-contained one
# they replaced, so trusts keep their client kits across the DNS cutover
# (FLIP#749). A staging estate provisioned with a fresh CA instead trips the
# fingerprint guard below, never a silent mint.
case "${ENV_NAME:-}" in
prod | stag) ;;
*)
    echo "❌ ENV_NAME must be 'prod' or 'stag' (the Makefile passes it from HUB; run via \`make add-fl-kits\`) — got '${ENV_NAME:-}'." >&2
    exit 1
    ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
NVFLARE_DIR="${REPO_ROOT}/fl-services/nvflare"
WORKSPACE_PARENT="${NVFLARE_DIR}/provision/workspace-${ENV_NAME}"
KEY_PREFIX="fl-flare-participant-kits/${FLARE_KIT_DATE}"
BASE_S3="s3://${AICENTRE_BUCKET_NAME}/${KEY_PREFIX}"

# A kit is complete on a net once these startup files are there: flip-api's
# FLARE_KIT_REQUIRED_FILES (db/seed/fl_kit_slots.py; a test keeps the two in step).
REQUIRED_STARTUP_FILES=(client.crt client.key rootCA.pem fed_client.json sub_start.sh)

log() { echo "🧩 $*"; }

# A mid-run abort must tell the operator what already reached S3. The ERR trap only
# covers command failures; guard aborts must go through die(), which routes their
# message through the same reporting (an explicit `exit 1` bypasses the trap, which
# would lose it on exactly the partial-run paths).
UPLOADED=()
on_error() {
    echo "" >&2
    echo "❌ add-fl-kits aborted." >&2
    if [[ ${#UPLOADED[@]} -gt 0 ]]; then
        echo "   Kits uploaded this run (the last may be partial): ${UPLOADED[*]}" >&2
        echo "   A kit is claimable only once it is complete on every net. Re-run add-fl-kits" >&2
        echo "   with the number of slots still needed: it numbers past every kit in S3." >&2
    else
        echo "   Nothing was minted or uploaded; re-run once the underlying error is fixed." >&2
    fi
}
trap on_error ERR

die() {
    printf '%s\n' "$@" >&2
    on_error
    exit 1
}

# --- 1. Discover nets + compute the new slot names -----------------------------

nets_listing="$(aws s3 ls "${BASE_S3}/")" || {
    echo "❌ Could not list ${BASE_S3}/ — AWS error above (credentials/profile?), not a config issue." >&2
    exit 1
}
# A read loop, not mapfile: an admin runs this on their own machine, and macOS
# ships bash 3.2, which has no mapfile.
NETS=()
while IFS= read -r net; do
    NETS+=("${net}")
done < <(printf '%s\n' "${nets_listing}" | awk '{print $2}' | sed -n 's#^\(net-[0-9]*\)/$#\1#p' | sort -V)
if [[ "${#NETS[@]}" -eq 0 ]]; then
    echo "❌ No net-* prefixes under ${BASE_S3}/ — is FLARE_KIT_DATE correct?" >&2
    exit 1
fi
log "Nets in S3 (${FLARE_KIT_DATE}): ${NETS[*]}"

# Existing slot names: every net's services/Trust_* prefixes. The max trailing number
# decides where new names start, so a name is never reused, even a half-uploaded one.
existing_names=()
for net in "${NETS[@]}"; do
    services_listing="$(aws s3 ls "${BASE_S3}/${net}/services/")" || {
        echo "❌ Could not list ${BASE_S3}/${net}/services/ — refusing to compute slot numbers" >&2
        echo "   from an incomplete view (a missed name here could reuse a live trust's slot)." >&2
        exit 1
    }
    while IFS= read -r name; do
        [[ -n "${name}" ]] && existing_names+=("${name}")
    done < <(printf '%s\n' "${services_listing}" | awk '{print $2}' | grep -oE '^Trust_[^/]+' || true)
done

max_num=0
for name in ${existing_names[@]+"${existing_names[@]}"}; do
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
for ((i = 1; i <= N; i++)); do
    MINT_NAMES+=("Trust_$((max_num + i))")
done

echo ""
echo "   Plan (${ENV_NAME}):  mint ${N} kit slot(s)"
echo "     bucket        ${AICENTRE_BUCKET_NAME} (kit date ${FLARE_KIT_DATE})"
echo "     nets          ${NETS[*]}"
echo "     mint          ${MINT_NAMES[*]}  (existing max: Trust_${max_num})"
echo ""
if [[ "${YES}" != "1" ]]; then
    read -r -p "   Proceed? [y/N] " answer
    [[ "${answer}" == "y" || "${answer}" == "Y" ]] || { echo "Aborted."; exit 1; }
fi

# --- 2. Per net: restore/verify the CA workspace, mint, upload ------------------

# One temp path, reused for every net's root-CA download and cleaned by a single
# EXIT trap. Bash keeps only one EXIT trap, so a per-net `trap ... EXIT` inside
# the loop would silently replace the previous net's and could leak its file.
s3_root_ca="$(mktemp -t rootCA.XXXXXX.pem)"
trap 'rm -f "${s3_root_ca}"' EXIT
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
    aws s3 cp "${BASE_S3}/${net}/${server_kit}/startup/rootCA.pem" "${s3_root_ca}" --only-show-errors
    local_fp="$(openssl x509 -in "${workspace}/${server_kit}/startup/rootCA.pem" -noout -fingerprint -sha256)"
    s3_fp="$(openssl x509 -in "${s3_root_ca}" -noout -fingerprint -sha256)"
    if [[ "${local_fp}" != "${s3_fp}" ]]; then
        die "❌ ${net}: local root CA does not match the live S3 kits — refusing to mint." \
            "   local: ${local_fp}" \
            "   s3:    ${s3_fp}" \
            "   The workspace at ${workspace} is not the one that provisioned this net."
    fi

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

        # Required startup files last: until they are all there, flip-api skips the kit.
        log "${net}: uploading ${name} (additive)..."
        UPLOADED+=("${net}/${name}")
        excludes=() includes=()
        for f in "${REQUIRED_STARTUP_FILES[@]}"; do
            excludes+=(--exclude "startup/${f}")
            includes+=(--include "startup/${f}")
        done
        aws s3 cp "${workspace}/services/${name}/" "${BASE_S3}/${net}/services/${name}/" \
            --recursive --only-show-errors "${excludes[@]}"
        aws s3 cp "${workspace}/services/${name}/" "${BASE_S3}/${net}/services/${name}/" \
            --recursive --only-show-errors --exclude '*' "${includes[@]}"
    done

    # Refresh the mirrored CA registry so the next add from a fresh checkout signs
    # with a registry that knows these identities.
    aws s3 cp "${workspace}/state/cert.json" "${BASE_S3}/${net}/state/cert.json" --only-show-errors
    log "${net}: done (${#MINT_NAMES[@]} kits minted + uploaded, state/cert.json refreshed)."
done

echo ""
log "Minted ${MINT_NAMES[*]}: claimable at the next trust registration — no Terraform apply, no restart."
