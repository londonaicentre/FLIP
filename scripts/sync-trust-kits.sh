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
# Refresh the Hub-shared block on existing trust kit files WITHOUT touching
# credentials. Read a JSON array on stdin (same shape as register_trust output,
# but credential fields MUST be absent -- sync explicitly never rotates
# TRUST_API_KEY / TRUST_INTERNAL_SERVICE_KEY). The Hub-shared section header is
# the byte-identical sentinel used by scripts/distribute-trust-kits.sh; both
# scripts upsert into the same block.
#
# Input contract (per kit object):
#   .fl_kit_slot         -- selects trust/.env.<slot> as the target file
#   .hub_shared          -- dict of KEY=VALUE pairs to upsert
#   .trust_api_key       -- MUST NOT be present (script exits 1 if it is)
#   .trust_internal_service_key -- MUST NOT be present (exits 1 if it is)
#
# Behaviour:
#   - For each kit: if trust/.env.<slot> is absent, log a warning and skip
#     (sync has nothing to preserve from an empty file). The kit file must
#     have been written by distribute-trust-kits.sh first.
#   - Append the Hub-shared section header on first sync; upsert each
#     hub_shared key on every sync. Idempotent.
#   - Empty input array is a no-op.

set -eo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TRUST_DIR="$REPO_ROOT/trust"

# Sentinel comment line that marks the start of the managed hub-shared block.
# Must be byte-identical to the sentinel in distribute-trust-kits.sh.
HUB_SHARED_HEADER='# -- Hub-shared (managed by register-trust / sync-trust-kits -- do not edit) --'

# Replace `KEY=...` in $file if present, else append `KEY=value`.
upsert_var() {
    local file="$1" key="$2" value="$3"
    if grep -qE "^${key}=" "$file"; then
        # `|` delimiter is safe: all current values (url-safe base64, UUIDs, ints,
        # slot names, URLs, header names, backend names) are `|`-free.
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
    echo "No kits to sync -- nothing to do."
    exit 0
fi

# Refuse credential fields up-front so a confused caller can't rotate creds.
if echo "$KITS_JSON" | jq -e '.[] | select(has("trust_api_key") or has("trust_internal_service_key"))' >/dev/null; then
    echo "sync-trust-kits.sh: input contains credential fields. Use distribute-trust-kits.sh to rotate." >&2
    exit 1
fi

echo "$KITS_JSON" | jq -c '.[]' | while read -r kit; do
    slot="$(echo "$kit" | jq -r '.fl_kit_slot')"
    target="$TRUST_DIR/.env.${slot}"

    if [ ! -f "$target" ]; then
        echo "WARNING: $target does not exist -- skipping (sync has no credentials to preserve)." >&2
        continue
    fi

    if ! grep -qF "$HUB_SHARED_HEADER" "$target"; then
        printf '\n%s\n' "$HUB_SHARED_HEADER" >> "$target"
    fi
    while IFS='=' read -r key value; do
        upsert_var "$target" "$key" "$value"
    done < <(echo "$kit" | jq -r '.hub_shared // {} | to_entries[] | "\(.key)=\(.value)"')

    echo "Synced hub-shared block in $target"
done
