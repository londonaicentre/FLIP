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
# Parity-check the four prefixes synced by `make migrate-flip-bucket`.
#
# Why: `aws s3 sync` exits 0 even when individual object copies fail
# (per-object KMS / throttle / RequestTimeout errors), so the sync target's
# green banner is not enough to safely decommission the source bucket.
#
# What we check: for each prefix, every key on the source must also exist
# on the destination (subset check, not equality). Destination is allowed
# to have additional keys — researchers / fl-server may have written to
# the new buckets after the env-var cutover, and those new uploads should
# not cause this verifier to fail.
#
# Inputs: FLIP_BUCKET_NAME (legacy source), FLIP_MODEL_FILES_UPLOADS_BUCKET_NAME,
# FLIP_FL_RESULTS_BUCKET_NAME, FLIP_APP_BUNDLES_BUCKET_NAME from the env file
# the caller already include'd (the Makefile does this for you).
#
# Exit codes: 0 = every src key found on dst (safe to decommission src).
# 1 = at least one src key missing from dst (data loss risk — do NOT
# decommission until investigated).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/utils.sh"

for var in FLIP_BUCKET_NAME FLIP_MODEL_FILES_UPLOADS_BUCKET_NAME \
           FLIP_FL_RESULTS_BUCKET_NAME FLIP_APP_BUNDLES_BUCKET_NAME; do
    if [ -z "${!var:-}" ]; then
        log_error "$var is not set; populate it in the env file before running"
        exit 1
    fi
done

# Parse `s3://bucket/path/` into ("bucket", "path/") via stdout (one per line).
# We pass these to `aws s3api list-objects-v2 --bucket --prefix` because that
# returns structured JSON — `aws s3 ls --recursive | awk '{print $4}'` (the
# obvious shorter alternative) silently truncates any S3 key containing a
# space, and model uploads use a user-supplied filename so spaces in keys
# are plausible. Truncation here would manufacture false negatives and
# could green-light a `aws s3 rb` on the legacy bucket with missing objects.
parse_s3_url() {
    local s3_path="$1"
    local bucket prefix
    bucket="$(echo "$s3_path" | sed -E 's|^s3://([^/]+)/.*|\1|')"
    prefix="$(echo "$s3_path" | sed -E 's|^s3://[^/]+/(.*)|\1|')"
    printf '%s\n%s\n' "$bucket" "$prefix"
}

# List all non-folder-marker keys under an s3:// path, strip the request
# prefix, emit one relative suffix per line, sorted. Folder markers
# (zero-byte objects with keys ending in `/`) are filtered because
# `aws s3 sync` intentionally does not copy them — leaving them in the
# count would manufacture a false off-by-one.
#
# `--page-size 1000` triggers auto-pagination across buckets with more than
# a single page of contents; without it the response is capped at 1000 keys.
list_suffixes() {
    local s3_path="$1"
    local bucket prefix
    {
        read -r bucket
        read -r prefix
    } < <(parse_s3_url "$s3_path")

    # Export PREFIX (rather than the `VAR=val cmd` prefix form) so the
    # downstream python3 in the pipeline inherits it. The prefix form only
    # applies to the immediate command.
    export PREFIX="$prefix"
    # No `2>/dev/null` on the aws call: `set -euo pipefail` already makes the
    # script abort on a non-zero exit, but suppressing stderr also hides the
    # underlying cause (ExpiredToken, AccessDenied, throttling) from the
    # operator. Let the AWS error message through.
    aws s3api list-objects-v2 \
        --bucket "$bucket" \
        --prefix "$prefix" \
        --page-size 1000 \
        --output json \
        | python3 -c '
import json, os, sys
prefix = os.environ["PREFIX"]
data = json.load(sys.stdin)
for obj in data.get("Contents") or []:
    key = obj["Key"]
    if obj.get("Size", 0) == 0 and key.endswith("/"):
        continue
    sys.stdout.write(key[len(prefix):] + "\n")
        ' \
        | sort
    unset PREFIX
}

ok=true
check() {
    local label="$1" src="$2" dst="$3"
    local src_keys dst_keys missing extras missing_count extras_count
    src_keys="$(list_suffixes "$src")"
    dst_keys="$(list_suffixes "$dst")"
    missing="$(comm -23 <(echo "$src_keys") <(echo "$dst_keys"))"
    extras="$(comm -13 <(echo "$src_keys") <(echo "$dst_keys"))"
    missing_count="$(printf '%s' "$missing" | grep -c . || true)"
    extras_count="$(printf '%s' "$extras" | grep -c . || true)"

    if [ "$missing_count" = "0" ]; then
        if [ "$extras_count" = "0" ]; then
            log_success "$label: exact parity (every source key present on destination)"
        else
            log_success "$label: every source key present on destination; $extras_count extra key(s) on destination (post-cutover writes)"
        fi
    else
        log_error "$label: $missing_count source key(s) NOT present on destination — first 10:"
        printf '%s\n' "$missing" | head -10 | sed 's/^/    /' >&2
        ok=false
    fi
}

check "model file uploads" \
    "s3://${FLIP_BUCKET_NAME}/model_files/uploaded/" \
    "s3://${FLIP_MODEL_FILES_UPLOADS_BUCKET_NAME}/uploaded/"
check "FL results" \
    "s3://${FLIP_BUCKET_NAME}/uploaded_federated_data/" \
    "s3://${FLIP_FL_RESULTS_BUCKET_NAME}/"
check "FL base apps" \
    "s3://${FLIP_BUCKET_NAME}/base-application/" \
    "s3://${FLIP_APP_BUNDLES_BUCKET_NAME}/base-application/"
check "FL app destinations" \
    "s3://${FLIP_BUCKET_NAME}/app_destination_bucket/" \
    "s3://${FLIP_APP_BUNDLES_BUCKET_NAME}/app_destinations/"

if [ "$ok" != "true" ]; then
    echo "" >&2
    log_error "One or more prefixes have source keys missing on the destination."
    log_error "Re-run \`make migrate-flip-bucket\` (the sync is safe to repeat)"
    log_error "or investigate the diff manually before deleting s3://${FLIP_BUCKET_NAME}."
    exit 1
fi

log_success "All prefixes verified — safe to decommission s3://${FLIP_BUCKET_NAME} after the cooldown."
