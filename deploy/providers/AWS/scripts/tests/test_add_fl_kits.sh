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

# Black-box tests for scripts/add_fl_kits.sh — the activate/mint split, the spare
# completeness guard, and the mint-loop recovery reporting.
#
# Drives the REAL script with `aws` / `make` / `openssl` stubbed on PATH (no credentials,
# no network) inside a throwaway repo skeleton, so the checkout's provisioning workspace
# is never touched. The pre-prompt scenarios abort at the confirmation and assert the
# printed Plan; the post-prompt scenarios run the mint loop to completion (YES=1) and
# assert the env-file append and the guard-abort recovery guidance.
#
# Usage:
#     bash deploy/providers/AWS/scripts/tests/test_add_fl_kits.sh

set -u

SCRIPT_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/add_fl_kits.sh"

TEST_ROOT="$(mktemp -d)"
trap 'rm -rf "${TEST_ROOT}"' EXIT

# The script derives REPO_ROOT (and so the workspace parent) from its own location —
# copy it into a repo skeleton under TEST_ROOT so mint-loop paths stay in the sandbox.
FAKE_REPO="${TEST_ROOT}/repo"
mkdir -p "${FAKE_REPO}/deploy/providers/AWS/scripts" "${FAKE_REPO}/fl-services/nvflare"
cp "${SCRIPT_SRC}" "${FAKE_REPO}/deploy/providers/AWS/scripts/add_fl_kits.sh"
SCRIPT="${FAKE_REPO}/deploy/providers/AWS/scripts/add_fl_kits.sh"
WORKSPACE_PARENT="${FAKE_REPO}/fl-services/nvflare/provision/workspace-stag"

MOCKBIN="${TEST_ROOT}/mockbin"
mkdir -p "${MOCKBIN}"

# Mock aws. Fixtures (per case, via FIXTURE_DIR):
#   nets                       — one net-N per line (s3 ls of the kit date prefix)
#   <net>.services             — one Trust_*/fl-server-* per line (s3 ls of a net's services/)
#   <net>.<name>.startup       — override a kit's startup/ file list (completeness check);
#                                default is a complete kit
#   <net>.<name>.keycount      — override the never-overwrite KeyCount probe; default 0 (empty)
cat > "${MOCKBIN}/aws" <<'MOCK_AWS'
#!/usr/bin/env bash
if [[ "$1" == "s3" && "$2" == "ls" ]]; then
    uri="$3"
    if [[ "$uri" =~ /(net-[0-9]+)/services/$ ]]; then
        net="${BASH_REMATCH[1]}"
        while read -r n; do [[ -n "$n" ]] && printf '                           PRE %s/\n' "$n"; done \
            < "${FIXTURE_DIR}/${net}.services"
        exit 0
    fi
    while read -r n; do [[ -n "$n" ]] && printf '                           PRE %s/\n' "$n"; done \
        < "${FIXTURE_DIR}/nets"
    exit 0
fi
if [[ "$1" == "s3api" && "$2" == "list-objects-v2" ]]; then
    prefix="" query=""
    while [[ $# -gt 0 ]]; do
        [[ "$1" == "--prefix" ]] && { prefix="$2"; shift; }
        [[ "$1" == "--query" ]] && { query="$2"; shift; }
        shift
    done
    if [[ "$query" == "Contents[].Key" ]]; then  # spare completeness check under startup/
        [[ "$prefix" =~ /(net-[0-9]+)/services/(Trust_[^/]+)/startup/$ ]] || exit 1
        fx="${FIXTURE_DIR}/${BASH_REMATCH[1]}.${BASH_REMATCH[2]}.startup"
        files="client.crt client.key rootCA.pem fed_client.json start.sh stop_fl.sh sub_start.sh"
        [[ -f "$fx" ]] && files="$(cat "$fx")"
        out=""
        for f in $files; do out="${out}${out:+	}${prefix}${f}"; done
        printf '%s\n' "$out"
        exit 0
    fi
    if [[ "$query" == "KeyCount" ]]; then  # never-overwrite probe on a mint target prefix
        [[ "$prefix" =~ /(net-[0-9]+)/services/(Trust_[^/]+)/$ ]] || exit 1
        fx="${FIXTURE_DIR}/${BASH_REMATCH[1]}.${BASH_REMATCH[2]}.keycount"
        count=0
        [[ -f "$fx" ]] && count="$(cat "$fx")"
        echo "${count}"
        exit 0
    fi
    echo "mock aws: unexpected s3api query: ${query}" >&2
    exit 1
fi
if [[ "$1" == "s3" && "$2" == "cp" ]]; then
    src="" dst=""
    shift 2
    for a in "$@"; do
        [[ "$a" == --* ]] && continue
        [[ -z "$src" ]] && { src="$a"; continue; }
        dst="$a"
    done
    if [[ "$dst" != s3://* ]]; then  # download: create the destination so later reads work
        mkdir -p "$(dirname "$dst")"
        : > "$dst"
    fi
    exit 0
fi
echo "mock aws: unexpected call: $*" >&2
exit 1
MOCK_AWS

# Mock make (the provision-add-client-<env> mint) and openssl (fingerprints always match —
# the mismatch path is a pure string compare, not worth real certs).
printf '#!/usr/bin/env bash\nexit 0\n' > "${MOCKBIN}/make"
printf '#!/usr/bin/env bash\necho "SHA256 Fingerprint=MO:CK"\nexit 0\n' > "${MOCKBIN}/openssl"
chmod +x "${MOCKBIN}/aws" "${MOCKBIN}/make" "${MOCKBIN}/openssl"

PASS=0
FAIL=0
# STARTUP_OVERRIDES / KEYCOUNT_OVERRIDES: "net Trust_X <value...>" lines, reset per case.
STARTUP_OVERRIDES=()
KEYCOUNT_OVERRIDES=()
YES_MODE=""

run_case() {
    local name="$1" N="$2" env_json="$3" nets="$4"
    shift 4
    local fx
    fx="$(mktemp -d --tmpdir="${TEST_ROOT}")"
    printf '%s\n' ${nets} | tr ' ' '\n' > "${fx}/nets"
    local spec net svcs
    for spec in "$@"; do
        net="${spec%%=*}" svcs="${spec#*=}"
        printf '%s\n' ${svcs} | tr ' ' '\n' > "${fx}/${net}.services"
    done
    local ov
    for ov in "${STARTUP_OVERRIDES[@]:-}"; do
        [[ -z "${ov}" ]] && continue
        echo "${ov}" | cut -d' ' -f3- > "${fx}/$(echo "${ov}" | awk '{print $1"."$2}').startup"
    done
    for ov in "${KEYCOUNT_OVERRIDES[@]:-}"; do
        [[ -z "${ov}" ]] && continue
        echo "${ov}" | cut -d' ' -f3- > "${fx}/$(echo "${ov}" | awk '{print $1"."$2}').keycount"
    done
    LAST_ENVF="$(mktemp --tmpdir="${TEST_ROOT}")"
    printf 'SOMETHING=1\nFL_KIT_SLOT_NAMES=%s\nOTHER=2\n' "${env_json}" > "${LAST_ENVF}"
    LAST_OUT="$(printf 'n\n' | env PATH="${MOCKBIN}:${PATH}" \
        N="${N}" PROD=stag AICENTRE_BUCKET_NAME=testbucket FLARE_KIT_DATE=2026-01-01 \
        MAIN_ENV_FILE="${LAST_ENVF}" FL_BACKEND=nvflare FIXTURE_DIR="${fx}" YES="${YES_MODE}" \
        bash "${SCRIPT}" 2>&1)"
    LAST_RC=$?
    LAST_ACT="$(printf '%s\n' "${LAST_OUT}" | sed -n 's/^     activate      //p')"
    LAST_MNT="$(printf '%s\n' "${LAST_OUT}" | sed -n 's/^     mint          //p')"
    echo "── ${name}"
    STARTUP_OVERRIDES=()
    KEYCOUNT_OVERRIDES=()
    YES_MODE=""
}

check() {  # assert the plan's activate/mint lines
    local label="$1" want_act="$2" want_mnt="$3"
    if [[ "${LAST_ACT}" == "${want_act}" && "${LAST_MNT}" == "${want_mnt}" ]]; then
        echo "   ✅ ${label}"
        PASS=$((PASS + 1))
    else
        echo "   ❌ ${label}"
        echo "      want activate=[${want_act}] mint=[${want_mnt}]"
        echo "      got  activate=[${LAST_ACT}] mint=[${LAST_MNT}]  rc=${LAST_RC}"
        FAIL=$((FAIL + 1))
    fi
}

check_refuses() {  # assert the run aborted (non-zero) with a substring in its output
    local label="$1" want_substr="$2"
    if [[ "${LAST_RC}" -ne 0 && "${LAST_OUT}" == *"${want_substr}"* ]]; then
        echo "   ✅ ${label}"
        PASS=$((PASS + 1))
    else
        echo "   ❌ ${label} (rc=${LAST_RC})"
        echo "      wanted non-zero exit + substring: ${want_substr}"
        echo "      got: ${LAST_OUT}"
        FAIL=$((FAIL + 1))
    fi
}

check_contains() {  # assert a substring, with the expected exit code
    local label="$1" want_rc="$2" want_substr="$3"
    if [[ "${LAST_RC}" -eq "${want_rc}" && "${LAST_OUT}" == *"${want_substr}"* ]]; then
        echo "   ✅ ${label}"
        PASS=$((PASS + 1))
    else
        echo "   ❌ ${label} (rc=${LAST_RC}, want ${want_rc})"
        echo "      wanted substring: ${want_substr}"
        echo "      got: ${LAST_OUT}"
        FAIL=$((FAIL + 1))
    fi
}

seed_workspace() {  # pre-seed a net's CA state + server kit so the mint loop skips the restore
    local net="$1"
    mkdir -p "${WORKSPACE_PARENT}/${net}/state" \
        "${WORKSPACE_PARENT}/${net}/services/fl-server-${net}/startup"
    echo '{}' > "${WORKSPACE_PARENT}/${net}/state/cert.json"
    echo 'mock' > "${WORKSPACE_PARENT}/${net}/services/fl-server-${net}/startup/rootCA.pem"
}

# --- Plan scenarios (abort at the confirmation prompt) --------------------------------

# 1. No spares — mint all.
run_case "no spares, mint all (N=2)" 2 '["Trust_1", "Trust_2"]' "net-1 net-2" \
    "net-1=Trust_1 Trust_2 fl-server-net-1" "net-2=Trust_1 Trust_2 fl-server-net-2"
check "activate none, mint Trust_3 Trust_4" "(none — no spare kits)" "Trust_3 Trust_4  (existing max: Trust_2)"

# 2. Spares < N — activate the spare, mint the rest.
run_case "spare<N (N=2, 1 spare)" 2 '["Trust_1", "Trust_2"]' "net-1 net-2" \
    "net-1=Trust_1 Trust_2 Trust_3" "net-2=Trust_1 Trust_2 Trust_3"
check "activate Trust_3, mint Trust_4" "Trust_3" "Trust_4  (existing max: Trust_3)"

# 3. Spares >= N — pure activation, no mint.
run_case "spare>=N (N=2, 3 spares)" 2 '["Trust_1", "Trust_2"]' "net-1 net-2" \
    "net-1=Trust_1 Trust_2 Trust_3 Trust_4 Trust_5" "net-2=Trust_1 Trust_2 Trust_3 Trust_4 Trust_5"
check "activate Trust_3 Trust_4 (lowest two), no mint" "Trust_3 Trust_4" "(none — spares cover N)"

# 4. Half-uploaded kit (net-1 only) is NOT a spare but bumps max_num.
run_case "half-uploaded bumps max (N=2)" 2 '["Trust_1", "Trust_2"]' "net-1 net-2" \
    "net-1=Trust_1 Trust_2 Trust_3 Trust_9" "net-2=Trust_1 Trust_2 Trust_3"
check "activate Trust_3, mint Trust_10 (past the half-uploaded Trust_9)" "Trust_3" "Trust_10  (existing max: Trust_9)"

# 5. Non-numeric Trust_K8s on every net but not in env — NOT auto-activated, doesn't affect max.
run_case "non-numeric not activated (N=1)" 1 '["Trust_1", "Trust_2"]' "net-1 net-2" \
    "net-1=Trust_1 Trust_2 Trust_K8s" "net-2=Trust_1 Trust_2 Trust_K8s"
check "activate none, mint Trust_3" "(none — no spare kits)" "Trust_3  (existing max: Trust_2)"

# 6. Numeric sort of spares (not lexical): Trust_3, Trust_9 before Trust_10.
run_case "numeric spare ordering (N=2)" 2 '["Trust_1", "Trust_2"]' "net-1 net-2" \
    "net-1=Trust_1 Trust_2 Trust_3 Trust_9 Trust_10" "net-2=Trust_1 Trust_2 Trust_3 Trust_9 Trust_10"
check "activate Trust_3 Trust_9 (numeric lowest two), no mint" "Trust_3 Trust_9" "(none — spares cover N)"

# 7. COMPLETENESS GUARD: a spare whose kit is complete on every net activates cleanly.
run_case "complete spare activates (N=1)" 1 '["Trust_1", "Trust_2"]' "net-1 net-2" \
    "net-1=Trust_1 Trust_2 Trust_3" "net-2=Trust_1 Trust_2 Trust_3"
check "activate Trust_3 (kit complete on both nets)" "Trust_3" "(none — spares cover N)"

# 8. COMPLETENESS GUARD: a spare present on every net but INCOMPLETE on net-2
#    (its final-net upload was interrupted — missing client.key) is REFUSED, not activated.
STARTUP_OVERRIDES=("net-2 Trust_3 client.crt rootCA.pem fed_client.json sub_start.sh")
run_case "incomplete spare refused (N=1)" 1 '["Trust_1", "Trust_2"]' "net-1 net-2" \
    "net-1=Trust_1 Trust_2 Trust_3" "net-2=Trust_1 Trust_2 Trust_3"
check_refuses "refuses: incomplete on net-2, missing client.key" \
    "spare Trust_3 is incomplete on net-2: missing startup/client.key"

# --- Mint-loop scenarios (YES=1, past the confirmation prompt) ------------------------

# 9. Full mint run: workspaces pre-seeded, mint + upload mocked — the run succeeds and
#    the minted name is appended to FL_KIT_SLOT_NAMES in the env file.
seed_workspace net-1
seed_workspace net-2
YES_MODE=1
run_case "full mint run appends the env file (N=1)" 1 '["Trust_1", "Trust_2"]' "net-1 net-2" \
    "net-1=Trust_1 Trust_2 fl-server-net-1" "net-2=Trust_1 Trust_2 fl-server-net-2"
check_contains "run succeeds and reports the mint" 0 "minted [Trust_3"
if grep -q '^FL_KIT_SLOT_NAMES=\["Trust_1", "Trust_2", "Trust_3"\]$' "${LAST_ENVF}"; then
    echo "   ✅ FL_KIT_SLOT_NAMES gains Trust_3"
    PASS=$((PASS + 1))
else
    echo "   ❌ FL_KIT_SLOT_NAMES gains Trust_3"
    echo "      env file now: $(cat "${LAST_ENVF}")"
    FAIL=$((FAIL + 1))
fi

# 10. RECOVERY GUIDANCE ON GUARD ABORT: net-1's kit mints + uploads, then the
#     never-overwrite guard trips on net-2. The abort must still print what was uploaded
#     and the do-not-re-run recovery — a guard `exit 1` bypasses the ERR trap, so this
#     asserts the die() path keeps the guidance on exactly the partial-run paths.
seed_workspace net-1
seed_workspace net-2
KEYCOUNT_OVERRIDES=("net-2 Trust_3 5")
YES_MODE=1
run_case "guard abort after upload keeps recovery guidance (N=1)" 1 '["Trust_1", "Trust_2"]' "net-1 net-2" \
    "net-1=Trust_1 Trust_2 fl-server-net-1" "net-2=Trust_1 Trust_2 fl-server-net-2"
check_refuses "refuses net-2 overwrite" "already has objects — refusing to overwrite"
check_refuses "reports the net-1 upload" "Kits already uploaded this run: net-1/Trust_3"
check_refuses "warns against the instinctive re-run" "Do NOT re-run add-fl-kits"

echo ""
echo "==== ${PASS} passed, ${FAIL} failed ===="
[[ "${FAIL}" -eq 0 ]]
