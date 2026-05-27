#!/usr/bin/env bash
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
# Black-box tests for scripts/sync-trust-kits.sh.
set -euo pipefail

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/sync-trust-kits.sh"
PASS=0; FAIL=0

assert_grep() {
    local file="$1" pattern="$2" label="$3"
    if grep -qE "$pattern" "$file"; then echo "  ✅ $label"; PASS=$((PASS + 1))
    else echo "  ❌ $label"; sed 's/^/    /' "$file"; FAIL=$((FAIL + 1)); fi
}

assert_no_grep() {
    local file="$1" pattern="$2" label="$3"
    if ! grep -qE "$pattern" "$file"; then echo "  ✅ $label"; PASS=$((PASS + 1))
    else echo "  ❌ $label (should NOT contain $pattern)"; FAIL=$((FAIL + 1)); fi
}

# Test 1: append hub-shared block to legacy kit file (no header yet).
echo "▶ sync appends hub-shared block on legacy kit (preserves creds)"
T1="$(mktemp -d)"
mkdir -p "$T1/trust" "$T1/scripts"
ln -s "$SCRIPT" "$T1/scripts/sync-trust-kits.sh"
cat > "$T1/trust/.env.Trust_1" <<EOF
TRUST_API_KEY=preserved-key
TRUST_INTERNAL_SERVICE_KEY=preserved-internal
FL_KIT_SLOT=Trust_1
FL_KIT_SLOT_NUMBER=1
EOF
SYNC_JSON='[{"trust_name":"Trust_1","fl_kit_slot":"Trust_1",'
SYNC_JSON+='"hub_shared":{"AES_KEY_BASE64":"new-key","FL_BACKEND":"flower"}}]'
echo "$SYNC_JSON" | bash "$T1/scripts/sync-trust-kits.sh" >/dev/null
KIT="$T1/trust/.env.Trust_1"
assert_grep "$KIT" '^TRUST_API_KEY=preserved-key$'        "credentials preserved"
assert_grep "$KIT" '^TRUST_INTERNAL_SERVICE_KEY=preserved-internal$' "internal service key preserved"
assert_grep "$KIT" '^AES_KEY_BASE64=new-key$'             "hub-shared added"
assert_grep "$KIT" 'Hub-shared \(managed'                 "header added"
rm -rf "$T1"

# Test 2: rotate hub-shared value on second run, do not touch creds.
echo "▶ sync rotates AES key without touching creds, idempotent"
T2="$(mktemp -d)"
mkdir -p "$T2/trust" "$T2/scripts"
ln -s "$SCRIPT" "$T2/scripts/sync-trust-kits.sh"
cat > "$T2/trust/.env.Trust_1" <<EOF
TRUST_API_KEY=preserved-key
EOF
JSON_V1='[{"trust_name":"Trust_1","fl_kit_slot":"Trust_1","hub_shared":{"AES_KEY_BASE64":"v1","FL_BACKEND":"flower"}}]'
JSON_V2='[{"trust_name":"Trust_1","fl_kit_slot":"Trust_1","hub_shared":{"AES_KEY_BASE64":"v2","FL_BACKEND":"nvflare"}}]'
echo "$JSON_V1" | bash "$T2/scripts/sync-trust-kits.sh" >/dev/null
echo "$JSON_V2" | bash "$T2/scripts/sync-trust-kits.sh" >/dev/null
KIT="$T2/trust/.env.Trust_1"
assert_grep "$KIT" '^AES_KEY_BASE64=v2$'           "AES_KEY rotated to v2"
assert_grep "$KIT" '^FL_BACKEND=nvflare$'          "FL_BACKEND rotated"
assert_grep "$KIT" '^TRUST_API_KEY=preserved-key$' "creds still untouched"
[ "$(grep -c '^AES_KEY_BASE64=' "$KIT")" -eq 1 ] && \
    { echo "  ✅ no duplicate AES_KEY_BASE64"; PASS=$((PASS + 1)); } || \
    { echo "  ❌ duplicate AES_KEY_BASE64"; FAIL=$((FAIL + 1)); }
[ "$(grep -c 'Hub-shared' "$KIT")" -eq 1 ] && \
    { echo "  ✅ exactly one Hub-shared header"; PASS=$((PASS + 1)); } || \
    { echo "  ❌ duplicate Hub-shared header"; FAIL=$((FAIL + 1)); }
rm -rf "$T2"

# Test 3: refuse input with credential fields.
echo "▶ sync refuses input with credentials"
T3="$(mktemp -d)"
mkdir -p "$T3/trust" "$T3/scripts"
ln -s "$SCRIPT" "$T3/scripts/sync-trust-kits.sh"
cat > "$T3/trust/.env.Trust_1" <<EOF
TRUST_API_KEY=preserved-key
EOF
BAD_JSON='[{"trust_name":"Trust_1","fl_kit_slot":"Trust_1","trust_api_key":"rotated","hub_shared":{}}]'
if echo "$BAD_JSON" | bash "$T3/scripts/sync-trust-kits.sh" 2>/dev/null; then
    echo "  ❌ should have errored on cred input"; FAIL=$((FAIL + 1))
else
    echo "  ✅ rejected credential input"; PASS=$((PASS + 1))
fi
assert_no_grep "$T3/trust/.env.Trust_1" '^TRUST_API_KEY=rotated$' "creds never rotated"
rm -rf "$T3"

# Test 4: missing target kit file is logged and skipped, not created.
echo "▶ sync skips missing kit files"
T4="$(mktemp -d)"
mkdir -p "$T4/trust" "$T4/scripts"
ln -s "$SCRIPT" "$T4/scripts/sync-trust-kits.sh"
JSON='[{"trust_name":"Trust_1","fl_kit_slot":"Trust_1","hub_shared":{"AES_KEY_BASE64":"v"}}]'
echo "$JSON" | bash "$T4/scripts/sync-trust-kits.sh" >/dev/null 2>&1 || true
[ ! -f "$T4/trust/.env.Trust_1" ] && \
    { echo "  ✅ missing kit file not created"; PASS=$((PASS + 1)); } || \
    { echo "  ❌ kit file created for non-existent slot"; FAIL=$((FAIL + 1)); }
rm -rf "$T4"

# Test 5: empty input array is a no-op.
echo "▶ empty array is a no-op"
T5="$(mktemp -d)"
mkdir -p "$T5/trust" "$T5/scripts"
ln -s "$SCRIPT" "$T5/scripts/sync-trust-kits.sh"
cat > "$T5/trust/.env.Trust_1" <<EOF
TRUST_API_KEY=untouched
EOF
echo "[]" | bash "$T5/scripts/sync-trust-kits.sh" >/dev/null
KIT="$T5/trust/.env.Trust_1"
assert_grep "$KIT" '^TRUST_API_KEY=untouched$' "empty array leaves kit unchanged"
rm -rf "$T5"

echo "—"; echo "PASS=$PASS  FAIL=$FAIL"; [ "$FAIL" -eq 0 ]
