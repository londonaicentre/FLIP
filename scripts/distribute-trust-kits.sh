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
# Read a JSON array of trust kits on stdin (the stdout of
# `flip_api.scripts.register_trust`) and write each one into its per-trust kit
# file `trust/.env.<fl_kit_slot>` in the working tree.
#
# For each kit:
#   - If trust/.env.<slot> is absent, seed it from trust/.env.<slot>.example
#     (so the host-local port/dir profile is present), or start an empty file.
#   - Credentials block (TRUST_API_KEY, TRUST_INTERNAL_SERVICE_KEY,
#     FL_KIT_SLOT, FL_KIT_SLOT_NUMBER, EXPECTED_TRUST_ID): upserted only when
#     the kit contains them (new registration). The skip path omits credentials
#     so an idempotent re-run does NOT clobber the existing kit file's creds.
#   - Hub-shared block: upserted unconditionally. A sentinel header comment
#     # ── Hub-shared (managed by register-trust / sync-trust-kits — do not edit) ──
#     is appended once (on first write); subsequent runs upsert the values in
#     place. The port/dir lines and any operator edits outside this block are
#     preserved.
#
# An empty input array (`[]`, i.e. a registration error) is a no-op; a
# skip-path kit (creds absent) still refreshes the hub-shared block.

set -eo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TRUST_DIR="$REPO_ROOT/trust"

# Replace `KEY=...` in $file if present, else append `KEY=value`.
upsert_var() {
    local file="$1" key="$2" value="$3"
    if grep -qE "^${key}=" "$file"; then
        # `|` delimiter is safe: all current values (url-safe base64, UUIDs, ints,
        # slot names, URLs (CENTRAL_HUB_API_URL), header names (TRUST_API_KEY_HEADER),
        # backend names (FL_BACKEND, DOCKER_*)) are `|`-free.
        sed -i "s|^${key}=.*|${key}=${value}|" "$file"
    else
        echo "${key}=${value}" >> "$file"
    fi
}

KITS_JSON="$(cat)"
# Tolerate leading build/log noise: keep the last line that looks like a JSON array.
KITS_JSON="$(echo "$KITS_JSON" | awk 'NF' | grep -E '^\[' | tail -n 1 || true)"
[ -n "$KITS_JSON" ] || KITS_JSON="[]"

NUM_KITS="$(echo "$KITS_JSON" | jq 'length')"
if [ "$NUM_KITS" = "0" ]; then
    echo "ℹ️  No kits to distribute (empty array — registration error?). Kit files unchanged."
    exit 0
fi

# Sentinel comment line that marks the start of the managed hub-shared block.
# distribute / sync scripts both look for this line to know whether to append vs. upsert.
HUB_SHARED_HEADER='# ── Hub-shared (managed by register-trust / sync-trust-kits — do not edit) ──'

echo "$KITS_JSON" | jq -c '.[]' | while read -r kit; do
    slot="$(echo "$kit" | jq -r '.fl_kit_slot')"
    target="$TRUST_DIR/.env.${slot}"
    example="$TRUST_DIR/.env.${slot}.example"

    if [ ! -f "$target" ]; then
        if [ -f "$example" ]; then
            cp "$example" "$target"
            echo "📋 Seeded $target from $(basename "$example")"
        else
            : > "$target"
            echo "📋 Created empty $target (no .example template for slot ${slot})"
        fi
    fi

    # Credentials block — only present on a new registration. The skip path omits
    # these fields so we do NOT clobber the operator's existing TRUST_API_KEY etc.
    if echo "$kit" | jq -e '.trust_api_key' >/dev/null; then
        upsert_var "$target" TRUST_API_KEY "$(echo "$kit" | jq -r '.trust_api_key')"
        upsert_var "$target" TRUST_INTERNAL_SERVICE_KEY "$(echo "$kit" | jq -r '.trust_internal_service_key')"
    fi
    # EXPECTED_TRUST_ID, FL_KIT_SLOT, FL_KIT_SLOT_NUMBER are metadata — present on
    # both paths (new-registration and skip). Upserted unconditionally so that
    # operators who did not have EXPECTED_TRUST_ID on first registration can recover
    # it via a subsequent sync without needing to re-register.
    upsert_var "$target" EXPECTED_TRUST_ID "$(echo "$kit" | jq -r '.trust_id')"
    upsert_var "$target" FL_KIT_SLOT "$(echo "$kit" | jq -r '.fl_kit_slot')"
    upsert_var "$target" FL_KIT_SLOT_NUMBER "$(echo "$kit" | jq -r '.fl_kit_slot_number')"

    # Hub-shared block — present on both new-registration and skip paths.
    # Add the header line on first write so a human reader sees the section is
    # machine-managed; the line is a comment so make's `include` treats it as a no-op.
    if ! grep -qF "$HUB_SHARED_HEADER" "$target"; then
        printf '\n%s\n' "$HUB_SHARED_HEADER" >> "$target"
    fi
    while IFS='=' read -r key value; do
        upsert_var "$target" "$key" "$value"
    done < <(echo "$kit" | jq -r '.hub_shared // {} | to_entries[] | "\(.key)=\(.value)"')

    chmod 600 "$target"

    echo "✅ Wrote kit for trust '$(echo "$kit" | jq -r '.trust_name')' → $target"
done

echo "✅ distribute-trust-kits complete."
