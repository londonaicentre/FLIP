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
# `flip_api.scripts.register_deploy_trusts`) and write each one into its
# per-trust kit file `trust/.env.<fl_kit_slot>` in the working tree.
#
# For each kit:
#   - If trust/.env.<slot> is absent, seed it from trust/.env.<slot>.example
#     (so the host-local port/dir profile is present), or start an empty file.
#   - Upsert the five credential keys (TRUST_API_KEY, TRUST_INTERNAL_SERVICE_KEY,
#     FL_KIT_SLOT, FL_KIT_SLOT_NUMBER, EXPECTED_TRUST_ID) — replace the line if
#     the key already exists, append it otherwise. The port/dir lines and any
#     operator edits are preserved.
#
# An empty input array (`[]`, i.e. every trust already registered) is a no-op.

set -eo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TRUST_DIR="$REPO_ROOT/trust"

# Replace `KEY=...` in $file if present, else append `KEY=value`.
upsert_var() {
    local file="$1" key="$2" value="$3"
    if grep -qE "^${key}=" "$file"; then
        # `|` delimiter is safe: values are url-safe base64 / UUIDs / ints / slot
        # names — none contain `|`.
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
    echo "ℹ️  No new trust registrations — kit files unchanged."
    exit 0
fi

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

    upsert_var "$target" TRUST_API_KEY "$(echo "$kit" | jq -r '.trust_api_key')"
    upsert_var "$target" TRUST_INTERNAL_SERVICE_KEY "$(echo "$kit" | jq -r '.trust_internal_service_key')"
    upsert_var "$target" FL_KIT_SLOT "$(echo "$kit" | jq -r '.fl_kit_slot')"
    upsert_var "$target" FL_KIT_SLOT_NUMBER "$(echo "$kit" | jq -r '.fl_kit_slot_number')"
    upsert_var "$target" EXPECTED_TRUST_ID "$(echo "$kit" | jq -r '.trust_id')"
    chmod 600 "$target"

    echo "✅ Wrote kit for trust '$(echo "$kit" | jq -r '.trust_name')' → $target"
done

echo "✅ distribute-trust-kits complete."
