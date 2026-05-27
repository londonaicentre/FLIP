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
# Black-box tests for scripts/distribute-trust-kits.sh.

set -euo pipefail

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/distribute-trust-kits.sh"
PASS=0
FAIL=0

assert_grep() {
    local file="$1" pattern="$2" label="$3"
    if grep -qE "$pattern" "$file"; then
        echo "  ✅ $label"
        PASS=$((PASS + 1))
    else
        echo "  ❌ $label — pattern '$pattern' not found in:"
        sed 's/^/    /' "$file"
        FAIL=$((FAIL + 1))
    fi
}

assert_no_grep() {
    local file="$1" pattern="$2" label="$3"
    if ! grep -qE "$pattern" "$file"; then
        echo "  ✅ $label"
        PASS=$((PASS + 1))
    else
        echo "  ❌ $label — pattern '$pattern' unexpectedly found in:"
        sed 's/^/    /' "$file"
        FAIL=$((FAIL + 1))
    fi
}

run_simple() {
    local tmp="$1" pre="$2" json="$3"
    mkdir -p "$tmp/trust" "$tmp/scripts"
    ln -s "$SCRIPT" "$tmp/scripts/distribute-trust-kits.sh"
    [ -n "$pre" ] && printf '%s' "$pre" > "$tmp/trust/.env.Trust_1"
    echo "$json" | bash "$tmp/scripts/distribute-trust-kits.sh" >/dev/null
}

# ─── Test 1: New registration writes creds + hub-shared block ─────────────────
echo "▶ new registration writes creds + hub-shared"
T1="$(mktemp -d)"
T1_JSON="$(jq -n -c '{
    trust_id:"abc", trust_name:"Trust_1",
    trust_api_key:"plain-api-1", trust_internal_service_key:"plain-int-1",
    fl_kit_slot:"Trust_1", fl_kit_slot_number:1,
    hub_shared:{AES_KEY_BASE64:"Zm9vYmFy", FL_BACKEND:"flower"}
}' | jq -c '[.]')"
run_simple "$T1" "" "$T1_JSON"
KIT="$T1/trust/.env.Trust_1"
assert_grep "$KIT" "^TRUST_API_KEY=plain-api-1$"    "creds written (TRUST_API_KEY)"
assert_grep "$KIT" "^TRUST_INTERNAL_SERVICE_KEY=plain-int-1$" "creds written (TRUST_INTERNAL_SERVICE_KEY)"
assert_grep "$KIT" "^AES_KEY_BASE64=Zm9vYmFy$"     "hub-shared AES_KEY_BASE64 written"
assert_grep "$KIT" "^FL_BACKEND=flower$"            "hub-shared FL_BACKEND written"
assert_grep "$KIT" "Hub-shared .managed"            "hub-shared header present"
rm -rf "$T1"

# ─── Test 2: Skip path preserves existing creds, upserts hub-shared ───────────
echo "▶ skip path preserves existing creds + upserts hub-shared"
T2="$(mktemp -d)"
run_simple "$T2" \
    "TRUST_API_KEY=preserved-from-earlier-run
TRUST_INTERNAL_SERVICE_KEY=preserved-internal
" \
    '[{"trust_id":"abc","trust_name":"Trust_1","fl_kit_slot":"Trust_1","hub_shared":{"AES_KEY_BASE64":"rotated"}}]'
KIT="$T2/trust/.env.Trust_1"
assert_grep "$KIT" "^TRUST_API_KEY=preserved-from-earlier-run$" "existing TRUST_API_KEY preserved"
assert_grep "$KIT" "^TRUST_INTERNAL_SERVICE_KEY=preserved-internal$" "existing TRUST_INTERNAL_SERVICE_KEY preserved"
assert_grep "$KIT" "^AES_KEY_BASE64=rotated$"       "hub-shared AES_KEY_BASE64 upserted on skip path"
rm -rf "$T2"

# ─── Test 3: Skip path does NOT write null creds ──────────────────────────────
echo "▶ skip path does not write null creds when creds absent from JSON"
T3="$(mktemp -d)"
run_simple "$T3" \
    "TRUST_API_KEY=keep-me
" \
    '[{"trust_id":"abc","trust_name":"Trust_1","fl_kit_slot":"Trust_1","hub_shared":{"AES_KEY_BASE64":"v"}}]'
KIT="$T3/trust/.env.Trust_1"
assert_no_grep "$KIT" "^TRUST_API_KEY=null$"        "null not written for TRUST_API_KEY"
assert_grep    "$KIT" "^TRUST_API_KEY=keep-me$"     "original TRUST_API_KEY untouched"
rm -rf "$T3"

# ─── Test 4: Hub-shared header upserted exactly once on repeated runs ─────────
echo "▶ header is upserted, not duplicated, on repeat"
JSON="$(jq -n -c '{
    trust_id:"abc", trust_name:"Trust_1",
    trust_api_key:"k", trust_internal_service_key:"i",
    fl_kit_slot:"Trust_1", fl_kit_slot_number:1,
    hub_shared:{AES_KEY_BASE64:"v"}
}' | jq -c '[.]')"
T4="$(mktemp -d)"
mkdir -p "$T4/trust" "$T4/scripts"
ln -s "$SCRIPT" "$T4/scripts/distribute-trust-kits.sh"
echo "$JSON" | bash "$T4/scripts/distribute-trust-kits.sh" >/dev/null
echo "$JSON" | bash "$T4/scripts/distribute-trust-kits.sh" >/dev/null
KIT="$T4/trust/.env.Trust_1"
HEADER_COUNT="$(grep -c "Hub-shared" "$KIT")"
if [ "$HEADER_COUNT" -eq 1 ]; then
    echo "  ✅ exactly one Hub-shared header (count=$HEADER_COUNT)"
    PASS=$((PASS + 1))
else
    echo "  ❌ expected 1 Hub-shared header, got $HEADER_COUNT:"
    sed 's/^/    /' "$KIT"
    FAIL=$((FAIL + 1))
fi
KEY_COUNT="$(grep -c "^AES_KEY_BASE64=" "$KIT")"
if [ "$KEY_COUNT" -eq 1 ]; then
    echo "  ✅ AES_KEY_BASE64 appears exactly once (count=$KEY_COUNT)"
    PASS=$((PASS + 1))
else
    echo "  ❌ expected AES_KEY_BASE64 once, got $KEY_COUNT:"
    sed 's/^/    /' "$KIT"
    FAIL=$((FAIL + 1))
fi
rm -rf "$T4"

# ─── Test 5: Hub-shared value rotates on subsequent run ───────────────────────
echo "▶ hub-shared value is updated (not duplicated) when rotated"
T5="$(mktemp -d)"
mkdir -p "$T5/trust" "$T5/scripts"
ln -s "$SCRIPT" "$T5/scripts/distribute-trust-kits.sh"
jq -n -c '[{trust_id:"abc", trust_name:"Trust_1",
    trust_api_key:"k", trust_internal_service_key:"i",
    fl_kit_slot:"Trust_1", fl_kit_slot_number:1,
    hub_shared:{AES_KEY_BASE64:"original"}}]' \
    | bash "$T5/scripts/distribute-trust-kits.sh" >/dev/null
jq -n -c '[{trust_id:"abc", trust_name:"Trust_1",
    fl_kit_slot:"Trust_1", hub_shared:{AES_KEY_BASE64:"rotated"}}]' \
    | bash "$T5/scripts/distribute-trust-kits.sh" >/dev/null
KIT="$T5/trust/.env.Trust_1"
assert_grep    "$KIT" "^AES_KEY_BASE64=rotated$"    "hub-shared value updated to rotated"
assert_no_grep "$KIT" "^AES_KEY_BASE64=original$"   "original hub-shared value removed"
rm -rf "$T5"

# ─── Test 6: Empty array is a no-op (does not touch kit files) ────────────────
echo "▶ empty array is a no-op"
T6="$(mktemp -d)"
mkdir -p "$T6/trust" "$T6/scripts"
ln -s "$SCRIPT" "$T6/scripts/distribute-trust-kits.sh"
printf 'TRUST_API_KEY=untouched\n' > "$T6/trust/.env.Trust_1"
echo "[]" | bash "$T6/scripts/distribute-trust-kits.sh" >/dev/null || true
KIT="$T6/trust/.env.Trust_1"
assert_grep "$KIT" "^TRUST_API_KEY=untouched$" "empty array leaves kit file unchanged"
rm -rf "$T6"

# ─── Test 7: FL_KIT_SLOT* written on skip path (no credentials in kit) ────────
echo "▶ FL_KIT_SLOT* upserted on skip path (regression: was inside creds if-block)"
T7="$(mktemp -d)"
T7_PRE="TRUST_API_KEY=existing-cred
"
T7_JSON="$(jq -n -c '{
    trust_id:"abc", trust_name:"Trust_1",
    fl_kit_slot:"Trust_1", fl_kit_slot_number:1,
    hub_shared:{AES_KEY_BASE64:"v"}
}' | jq -c '[.]')"
run_simple "$T7" "$T7_PRE" "$T7_JSON"
KIT="$T7/trust/.env.Trust_1"
assert_grep "$KIT" "^FL_KIT_SLOT=Trust_1$"    "FL_KIT_SLOT written on skip path"
assert_grep "$KIT" "^FL_KIT_SLOT_NUMBER=1$"   "FL_KIT_SLOT_NUMBER written on skip path"
assert_grep "$KIT" "^TRUST_API_KEY=existing-cred$" "existing cred preserved on skip path"
rm -rf "$T7"

# ─── Summary ──────────────────────────────────────────────────────────────────
echo "—"
echo "PASS=$PASS  FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
