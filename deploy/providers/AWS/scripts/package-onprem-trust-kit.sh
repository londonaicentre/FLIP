#!/bin/bash
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
# Package a ready-to-ship trust kit (env file + FL participant kit slice)
# for an on-prem trust operator. The packager does NOT edit the kit file —
# it only tarballs the existing trust/.env.<KIT> (already populated by the
# admin) together with the operator's slice of the FL participant kit S3
# bucket.
#
# Admin pre-steps (run before this script):
#   1. Open the prod UI, click Add Trust, fill in the trust's name/code/region.
#      Copy the 5 KEY=VALUE lines the modal surfaces ONCE (TRUST_API_KEY,
#      TRUST_INTERNAL_SERVICE_KEY, FL_KIT_SLOT, FL_KIT_SLOT_NUMBER,
#      EXPECTED_TRUST_ID) and paste them into trust/.env.<KIT>, replacing
#      the <run-make-register-trusts> placeholders.
#   2. Run `make sync-trust-kit-N` (from the repo root) to populate the
#      Hub-shared block in trust/.env.<KIT>, replacing the
#      <run-make-sync-trust-kit> placeholders.
#
# This script's job (admin runs from deploy/providers/AWS with prod AWS creds):
#   3. Validate the kit file has no unfilled placeholders.
#   4. Read FL_BACKEND, FLARE_KIT_DATE / FLOWER_KIT_DATE, FL_KIT_SLOT,
#      FL_KIT_SLOT_NUMBER, UPLOADED_FEDERATED_DATA_BUCKET out of the kit file.
#   5. Sync only this operator's portion of the FL participant kit out of S3
#      (net-1/services/<slot>/ for nvflare; net-1/certificates/ + one
#      supernode_credentials_<N> for flower).
#   6. Tarball trust/.env.<KIT> + fl-kit/ into
#      deploy/providers/AWS/build/trust-kits/flip-trust-kit-<slot>-<date>.tar.gz.
#
# Operator post-steps (after receiving the tarball over encrypted channel):
#   7. Extract, copy .env.<slot> into trust/, edit Host-local profile +
#      Trust-local credentials sections (set FL_KIT_DIR to the extracted
#      fl-kit/ absolute path, override ports/dirs/passwords as needed for
#      their host), then `make up-onprem-trust KIT=<slot>`.

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/utils.sh"

check_aws_profile

if [ -z "${KIT:-}" ]; then
    log_error "KIT=<slot> is required (e.g. KIT=Trust_2)."
    exit 1
fi

KIT_FILE="$REPO_ROOT/trust/.env.${KIT}"
if [ ! -f "$KIT_FILE" ]; then
    log_error "Kit file $KIT_FILE not found."
    log_error "Copy the template and fill it in first:"
    log_error "    cp trust/.env.${KIT}.production.example trust/.env.${KIT}"
    exit 1
fi

# Refuse to package an unfinished kit — the operator would just hit the same
# placeholder guard on their end. Tell the admin which step they skipped.
# Anchor on `^KEY=<run-make-` so comment lines that *mention* the placeholder
# in prose (the template's leading doc block does, multiple times) don't
# trip a false positive.
if grep -qE '^[A-Z0-9_]+=<run-make-' "$KIT_FILE"; then
    n="$(grep -cE '^[A-Z0-9_]+=<run-make-' "$KIT_FILE")"
    log_error "$KIT_FILE still has $n unfilled <run-make-…> placeholder(s)."
    log_error "Fill them in first:"
    log_error "  - Kit credentials (5 lines): UI → Add Trust → paste the modal output."
    log_error "  - Hub-shared block (12 lines): run 'make sync-trust-kit KIT=<slot> PROD=true' from repo root."
    exit 1
fi

# get_var KEY — extract the first matching KEY=… line's value from the kit file.
get_var() {
    sed -n "s/^${1}=//p" "$KIT_FILE" | head -1
}

fl_backend="$(get_var FL_BACKEND)"
bucket_raw="$(get_var UPLOADED_FEDERATED_DATA_BUCKET)"
slot="$(get_var FL_KIT_SLOT)"
slot_number="$(get_var FL_KIT_SLOT_NUMBER)"

# Validate the values that actually drive S3 paths. These are populated by
# the admin's UI/sync steps; missing values mean those steps didn't run.
for k in FL_BACKEND UPLOADED_FEDERATED_DATA_BUCKET FL_KIT_SLOT FL_KIT_SLOT_NUMBER; do
    if [ -z "$(get_var "$k")" ]; then
        log_error "$k is empty in $KIT_FILE — re-run 'make sync-trust-kit-N' first."
        exit 1
    fi
done

if [ "$fl_backend" = "nvflare" ]; then
    fl_kit_date="$(get_var FLARE_KIT_DATE)"
    fl_prefix="fl-flare-participant-kits"
    date_var="FLARE_KIT_DATE"
else
    fl_kit_date="$(get_var FLOWER_KIT_DATE)"
    fl_prefix="fl-flower-participant-kits"
    date_var="FLOWER_KIT_DATE"
fi
if [ -z "$fl_kit_date" ]; then
    log_error "$date_var is empty in $KIT_FILE — re-run 'make sync-trust-kit-N' first."
    exit 1
fi

# Slot in the kit file must match KIT — the FL kit S3 source is keyed on
# slot and we'd otherwise package the wrong sub-tree.
if [ "$slot" != "$KIT" ]; then
    log_error "FL_KIT_SLOT='$slot' in $KIT_FILE does not match KIT='$KIT'."
    log_error "Re-run with KIT=$slot (or fix the kit file)."
    exit 1
fi

# UPLOADED_FEDERATED_DATA_BUCKET may carry an `s3://` prefix or a path suffix
# (e.g. `s3://flipprod/uploaded-federated-data`). Reduce to the bucket name.
bucket_name="${bucket_raw#s3://}"
bucket_name="${bucket_name%%/*}"

BUILD_DIR="$REPO_ROOT/deploy/providers/AWS/build/trust-kits"
mkdir -p "$BUILD_DIR"
DATE="$(date -u +'%Y%m%d-%H%M%S')"
STAGE_DIR="$BUILD_DIR/${slot}-${DATE}"
KIT_STAGE_DIR="$STAGE_DIR/fl-kit"
mkdir -p "$KIT_STAGE_DIR"

# --- Slice the operator's FL participant kit ---
log_info "Syncing FL kit slice from S3 ($fl_backend; bucket=$bucket_name; date=$fl_kit_date; slot=$slot)..."
if [ "$fl_backend" = "nvflare" ]; then
    target="$KIT_STAGE_DIR/net-1/services/$slot"
    mkdir -p "$target"
    aws_cmd s3 sync "s3://$bucket_name/$fl_prefix/$fl_kit_date/net-1/services/$slot/" "$target/"
else
    mkdir -p "$KIT_STAGE_DIR/net-1/certificates" "$KIT_STAGE_DIR/net-1/keys"
    aws_cmd s3 sync "s3://$bucket_name/$fl_prefix/$fl_kit_date/net-1/certificates/" "$KIT_STAGE_DIR/net-1/certificates/"
    aws_cmd s3 cp \
        "s3://$bucket_name/$fl_prefix/$fl_kit_date/net-1/keys/supernode_credentials_$slot_number" \
        "$KIT_STAGE_DIR/net-1/keys/"
fi
log_success "FL kit staged at $KIT_STAGE_DIR"

# --- Copy the kit file as-is (operator edits Host-local on their end) ---
cp "$KIT_FILE" "$STAGE_DIR/.env.${slot}"
chmod 600 "$STAGE_DIR/.env.${slot}"
log_info "Copied $KIT_FILE → $STAGE_DIR/.env.${slot} (unmodified)."

# --- Tarball ---
tarball="$BUILD_DIR/flip-trust-kit-${slot}-${DATE}.tar.gz"
log_info "Packaging tarball..."
tar -C "$STAGE_DIR" -czf "$tarball" ".env.${slot}" fl-kit
chmod 600 "$tarball"
log_success "Tarball written to $tarball"

# --- Operator instructions ---
cat <<EOF

════════════════════════════════════════════════════════════════════════
  Trust kit packaged for slot '$slot' ($fl_backend backend)
════════════════════════════════════════════════════════════════════════

  Tarball:  $tarball
  Size:     $(du -h "$tarball" | cut -f1)
  Contains: .env.${slot}  (your filled-in trust/.env.${slot}, copied as-is)
            fl-kit/       (FL participant kit, only this slot's portion)

  SEND TO OPERATOR over an encrypted channel (Signal / Keybase / age-
  encrypted attachment). Tarball contains plaintext trust API key, FL TLS
  private key, AES encryption key — never send over unencrypted email.

  Operator-side instructions (paste these to them):
  ────────────────────────────────────────────────────────────────────────

  1. Receive flip-trust-kit-${slot}-${DATE}.tar.gz, save it to a private
     location (e.g. \$HOME). Extract:

       mkdir -p \$HOME/flip-trust-kit
       tar -C \$HOME/flip-trust-kit -xzf flip-trust-kit-${slot}-${DATE}.tar.gz

  2. From your FLIP checkout, copy the env file in:

       cp \$HOME/flip-trust-kit/.env.${slot} trust/.env.${slot}

  3. Edit trust/.env.${slot} — Host-local profile + Trust-local credentials
     sections only (managed blocks below them are already correct):
       - FL_KIT_DIR=\$HOME/flip-trust-kit/fl-kit
       - Adjust ports / bind-mount dirs to match your host
       - Override Orthanc / OMOP / XNAT / Grafana passwords for
         production-grade secrets

  4. Send your public IP to the FLIP admin so they can open the NLB:
       curl -s https://api.ipify.org

  5. Bring up the stack:

       make up-onprem-trust KIT=${slot}

════════════════════════════════════════════════════════════════════════

EOF

log_info "Stage dir retained at $STAGE_DIR (safe to delete after handoff)."
