#!/usr/bin/env bash
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
# On-prem trust readiness checklist. Diagnoses what's still missing in the
# operator's setup before `make up-onprem-trust KIT=<slot>` will succeed,
# in a single screenful: public IP, docker swarm state, kit file presence,
# Hub-shared block, Kit credentials, FL_KIT_DIR + its on-disk contents.
# Each line shows ✅ or ❌; on ❌ the line below it shows the concrete fix.
#
# Runs every check even when the kit file is absent — a first-time operator
# can run `make onboard-onprem-trust` straight after cloning the repo and
# see what they still need to ask the FLIP admin for. Checks that depend
# on kit-file values are marked ⏳ pending when the kit file is missing.
#
# Exits 0 when every check passes (i.e. `make up-onprem-trust` will work),
# 1 when any check fails. The root Makefile's `up-onprem-trust` target
# wraps this script as a precheck so an operator gets diagnostics instead
# of a cryptic compose / pydantic failure deeper in the stack.

set -eo pipefail

# Default to the conventional on-prem slot when no KIT is given so a first-
# time operator can run `make onboard-onprem-trust` straight from a fresh
# clone. Override on the command line for any other slot.
KIT="${1:-Trust_2}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KIT_FILE="$REPO_ROOT/trust/.env.${KIT}"

get_var() {
    [ -f "$KIT_FILE" ] || return 0
    sed -n "s/^${1}=//p" "$KIT_FILE" | head -1
}

# Treat both "absent" and "still <run-make-…> placeholder" as missing.
is_filled() {
    local v="$1"
    [ -n "$v" ] && [[ "$v" != *'<run-make-'* ]]
}

ok=true
pass()    { echo "  ✅ $*"; }
fail()    { echo "  ❌ $*"; ok=false; }
pending() { echo "  ⏳ $*"; ok=false; }
hint()    { echo "       → $*"; }

echo ""
echo "═══════════════════════════════════════════════════════════════════════"
echo "  On-prem trust onboarding checklist — kit trust/.env.${KIT}"
if [ "$KIT" = "Trust_2" ] && [ -z "${1:-}" ]; then
    echo "  (KIT defaulted to Trust_2 — override with: make onboard-onprem-trust KIT=<slot>)"
fi
echo "═══════════════════════════════════════════════════════════════════════"

# --- Public IP (informational, always shown) ---
OPERATOR_IP="$(curl -sf --max-time 5 https://api.ipify.org || echo '<could not detect — set it manually>')"
echo ""
echo "  Your public IP:  $OPERATOR_IP"
echo "  Send this to the FLIP admin so they can open the prod FL-server NLB:"
echo "      AWS_PROFILE=prod make allow-local-trust-nlb LOCAL_TRUST_IP=$OPERATOR_IP PROD=true"
echo ""
echo "  Checks:"
echo ""

# --- 1. Docker swarm (overlay networks require swarm mode; independent of kit file) ---
swarm_state="$(docker info --format '{{.Swarm.LocalNodeState}}' 2>/dev/null || echo 'inactive')"
if [ "$swarm_state" = "active" ]; then
    pass "Docker swarm:               active on this host"
else
    fail "Docker swarm:               $swarm_state (overlay networks need swarm mode)"
    hint "One-off host setup: docker swarm init"
fi

# --- 2. Kit file present ---
kit_present=false
if [ -f "$KIT_FILE" ]; then
    pass "Kit file present:           trust/.env.${KIT}"
    kit_present=true
else
    fail "Kit file MISSING:           trust/.env.${KIT}"
    hint "Ask the FLIP admin to package + send your kit ('make package-onprem-trust-kit"
    hint "  KIT=${KIT}' from deploy/providers/AWS), extract the tarball, then:"
    hint "    cp <extracted-dir>/.env.${KIT} trust/.env.${KIT}"
fi

# --- 3. Hub-shared block (14 keys; placed by admin's sync-trust-kit-N) ---
hub_keys=(
    AES_KEY_BASE64 CENTRAL_HUB_API_URL TRUST_API_KEY_HEADER FL_BACKEND
    FLOWER_KIT_DATE FLARE_KIT_DATE DOCKER_TAG DOCKER_REGISTRY
    DOCKER_FL_TAG DOCKER_FL_REGISTRY DOCKER_FL_CLIENT_NAME
    UPLOADED_FEDERATED_DATA_BUCKET NLB_SUBDOMAIN FL_SERVER_PORT
)
if ! $kit_present; then
    pending "Hub-shared block (14 keys): pending — needs kit file"
else
    hub_missing=()
    for k in "${hub_keys[@]}"; do
        if ! is_filled "$(get_var "$k")"; then
            hub_missing+=("$k")
        fi
    done
    hub_url="$(get_var CENTRAL_HUB_API_URL)"
    hub_url_display="${hub_url%/api}"
    if [ ${#hub_missing[@]} -eq 0 ]; then
        pass "Hub-shared block (14 keys): populated"
        echo "       UI URL:               ${hub_url_display:-<unset>}"
    else
        fail "Hub-shared block (14 keys): ${#hub_missing[@]} unfilled: ${hub_missing[*]}"
        hint "Ask the FLIP admin to run 'make sync-trust-kit-N' and re-send the kit."
    fi
fi

# --- 4. Kit credentials (5 keys; placed by admin's UI Add-Trust modal) ---
cred_keys=(TRUST_API_KEY TRUST_INTERNAL_SERVICE_KEY FL_KIT_SLOT FL_KIT_SLOT_NUMBER EXPECTED_TRUST_ID)
if ! $kit_present; then
    pending "Kit credentials (5 keys):   pending — needs kit file"
else
    cred_missing=()
    for k in "${cred_keys[@]}"; do
        if ! is_filled "$(get_var "$k")"; then
            cred_missing+=("$k")
        fi
    done
    if [ ${#cred_missing[@]} -eq 0 ]; then
        pass "Kit credentials (5 keys):   populated"
    else
        fail "Kit credentials (5 keys):   ${#cred_missing[@]} unfilled: ${cred_missing[*]}"
        hint "Ask the FLIP admin to UI-register your trust (Add Trust modal),"
        hint "  paste the 5 lines into trust/.env.${KIT}, and re-send the kit."
    fi
fi

# --- 5. FL_KIT_DIR set ---
fl_kit_dir="$(get_var FL_KIT_DIR)"
if ! $kit_present; then
    pending "FL_KIT_DIR:                 pending — needs kit file"
elif [ -n "$fl_kit_dir" ]; then
    pass "FL_KIT_DIR set:             $fl_kit_dir"
else
    fail "FL_KIT_DIR not set in kit file"
    hint "Add FL_KIT_DIR=<absolute path> to trust/.env.${KIT}"
fi

# --- 6. FL_KIT_DIR exists on disk ---
if ! $kit_present; then
    pending "FL_KIT_DIR on disk:          pending — needs kit file"
elif [ -n "$fl_kit_dir" ]; then
    if [ -d "$fl_kit_dir" ]; then
        pass "FL_KIT_DIR exists on disk:  $fl_kit_dir"
    else
        fail "FL_KIT_DIR not on disk:     $fl_kit_dir"
        hint "Extract the FL kit tarball from the FLIP admin at this path,"
        hint "  preserving the net-1/ hierarchy."
    fi
fi

# --- 7. FL kit contents (per-backend) ---
fl_backend="$(get_var FL_BACKEND)"
slot="$(get_var FL_KIT_SLOT)"
slot_number="$(get_var FL_KIT_SLOT_NUMBER)"
if ! $kit_present; then
    pending "FL kit contents:             pending — needs kit file"
elif [ -n "$fl_kit_dir" ] && [ -d "$fl_kit_dir" ] && [ -n "$fl_backend" ] && [ -n "$slot" ]; then
    if [ "$fl_backend" = "nvflare" ]; then
        target="$fl_kit_dir/net-1/services/$slot"
        if [ -d "$target/local" ] && [ -d "$target/startup" ] && [ -d "$target/transfer" ]; then
            pass "FL kit contents (nvflare):  $target/{local,startup,transfer}"
        else
            fail "FL kit contents (nvflare):  missing $target/{local,startup,transfer}"
            hint "Check the tarball was extracted preserving the net-1/services/<slot>/ hierarchy."
        fi
    else
        certs="$fl_kit_dir/net-1/certificates"
        creds="$fl_kit_dir/net-1/keys/supernode_credentials_$slot_number"
        if [ -d "$certs" ] && [ -f "$creds" ]; then
            pass "FL kit contents (flower):   $certs/ + supernode_credentials_$slot_number"
        else
            missing=""
            [ ! -d "$certs" ] && missing="$missing $certs/"
            [ ! -f "$creds" ] && missing="$missing $creds"
            fail "FL kit contents (flower):  missing:$missing"
            hint "Check the tarball was extracted preserving the net-1/{certificates,keys}/ hierarchy."
        fi
    fi
fi

echo ""
if $ok; then
    echo "═══════════════════════════════════════════════════════════════════════"
    echo "  Status: READY ✅"
    echo "═══════════════════════════════════════════════════════════════════════"
    echo ""
    echo "  Bring the stack up:"
    echo "      make up-onprem-trust KIT=${KIT}"
    echo ""
    exit 0
else
    echo "═══════════════════════════════════════════════════════════════════════"
    echo "  Status: NOT READY — fix the ❌ items and ⏳ pending steps above"
    echo "═══════════════════════════════════════════════════════════════════════"
    echo ""
    exit 1
fi
