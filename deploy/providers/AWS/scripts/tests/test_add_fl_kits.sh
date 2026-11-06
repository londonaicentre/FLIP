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

# Black-box tests for scripts/add_fl_kits.sh — slot numbering, net discovery, the
# required-files-last upload order, and the mint-loop recovery reporting.
#
# Drives the REAL script with `aws` / `make` / `openssl` stubbed on PATH (no credentials,
# no network) inside a throwaway repo skeleton, so the checkout's provisioning workspace
# is never touched. The plan scenarios abort at the confirmation and assert the printed
# Plan; the mint scenarios run to completion (YES=1).
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
#   nets                  — one top-level prefix per line (s3 ls of the kit date prefix)
#   <net>.services        — one Trust_*/fl-server-* per line (s3 ls of a net's services/)
#   <net>.<name>.keycount — override the never-overwrite KeyCount probe; default 0 (empty)
#   uploads               — written by the mock: one line per upload to s3, with its args
cat > "${MOCKBIN}/aws" <<'MOCK_AWS'
#!/usr/bin/env bash
if [[ "$1" == "s3" && "$2" == "ls" ]]; then
    fx="${FIXTURE_DIR}/nets"
    [[ "$3" =~ /(net-[0-9]+)/services/$ ]] && fx="${FIXTURE_DIR}/${BASH_REMATCH[1]}.services"
    while read -r n; do [[ -n "$n" ]] && printf '                           PRE %s/\n' "$n"; done < "$fx"
    exit 0
fi
if [[ "$1" == "s3api" && "$2" == "list-objects-v2" ]]; then  # never-overwrite KeyCount probe
    prefix=""
    while [[ $# -gt 0 ]]; do [[ "$1" == "--prefix" ]] && prefix="$2"; shift; done
    [[ "$prefix" =~ /(net-[0-9]+)/services/(Trust_[^/]+)/$ ]] || exit 1
    fx="${FIXTURE_DIR}/${BASH_REMATCH[1]}.${BASH_REMATCH[2]}.keycount"
    if [[ -f "$fx" ]]; then cat "$fx"; else echo 0; fi
    exit 0
fi
if [[ "$1" == "s3" && "$2" == "cp" ]]; then
    shift 2
    src="" dst="" skip=""
    for a in "$@"; do
        if [[ -n "$skip" ]]; then skip=""; continue; fi
        [[ "$a" == --exclude || "$a" == --include ]] && { skip=1; continue; }
        [[ "$a" == --* ]] && continue
        [[ -z "$src" ]] && { src="$a"; continue; }
        dst="$a"
    done
    if [[ "$dst" == s3://*/services/* ]]; then
        echo "${dst#*/fl-flare-participant-kits/*/} $*" >> "${FIXTURE_DIR}/uploads"
    elif [[ "$dst" != s3://* ]]; then  # download: create the destination so later reads work
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
KEYCOUNT_OVERRIDES=()  # "net Trust_X <count>" lines, reset per case
YES_MODE=""
ENV_MODE=""  # ENV_NAME for the next run_case (default stag; the Makefile passes it from HUB via deploy/env_mode.mk)

# run_case <label> <N> <top-level prefixes> <net=services...>...
run_case() {
    local name="$1" N="$2" nets="$3" spec ov
    shift 3
    FX="$(mktemp -d "${TEST_ROOT}/fx.XXXXXX")"
    printf '%s\n' ${nets} > "${FX}/nets"
    for spec in "$@"; do
        printf '%s\n' ${spec#*=} > "${FX}/${spec%%=*}.services"
    done
    for ov in "${KEYCOUNT_OVERRIDES[@]:-}"; do
        [[ -n "${ov}" ]] && echo "${ov##* }" > "${FX}/$(echo "${ov}" | awk '{print $1"."$2}').keycount"
    done
    LAST_OUT="$(printf 'n\n' | env PATH="${MOCKBIN}:${PATH}" \
        N="${N}" ENV_NAME="${ENV_MODE:-stag}" AICENTRE_BUCKET_NAME=testbucket FLARE_KIT_DATE=2026-01-01 \
        FL_BACKEND=nvflare FIXTURE_DIR="${FX}" YES="${YES_MODE}" \
        bash "${SCRIPT}" 2>&1)"
    LAST_RC=$?
    echo "── ${name}"
    KEYCOUNT_OVERRIDES=()
    YES_MODE=""
    ENV_MODE=""
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

check_plan() {  # assert the plan's mint line (the run aborts at the prompt)
    check_contains "$1" 1 "     mint          $2"
}

check_last_upload() {  # assert a kit's final upload carries exactly the required startup files
    local label="$1" kit="$2" last
    last="$(grep "^${kit}/ " "${FX}/uploads" 2>/dev/null | tail -1)"
    if [[ "${last}" == *"--exclude * --include startup/client.crt --include startup/client.key --include startup/rootCA.pem --include startup/fed_client.json --include startup/sub_start.sh" ]]; then
        echo "   ✅ ${label}"
        PASS=$((PASS + 1))
    else
        echo "   ❌ ${label}"
        echo "      uploads: $(cat "${FX}/uploads" 2>/dev/null)"
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

run_case "mint the next names (N=2)" 2 "net-1 net-2" \
    "net-1=Trust_1 Trust_2 fl-server-net-1" "net-2=Trust_1 Trust_2 fl-server-net-2"
check_plan "mint Trust_3 Trust_4" "Trust_3 Trust_4  (existing max: Trust_2)"

# A name on one net only (a half-uploaded kit) still bumps the numbering, so it is never reused.
run_case "half-uploaded name bumps max (N=1)" 1 "net-1 net-2" \
    "net-1=Trust_1 Trust_2 Trust_9" "net-2=Trust_1 Trust_2"
check_plan "mint Trust_10" "Trust_10  (existing max: Trust_9)"

run_case "non-numeric name does not affect max (N=1)" 1 "net-1 net-2" \
    "net-1=Trust_1 Trust_2 Trust_K8s" "net-2=Trust_1 Trust_2 Trust_K8s"
check_plan "mint Trust_3" "Trust_3  (existing max: Trust_2)"

# Zero-padded suffixes parse as decimal: bash arithmetic reads leading-zero numerals as
# octal, so Trust_008 crashed the max_num comparison ("value too great for base") and
# Trust_010 would compute max as 8. flip-api accepts such names (Python int() is decimal).
run_case "zero-padded suffixes parse as decimal (N=1)" 1 "net-1 net-2" \
    "net-1=Trust_008 Trust_010" "net-2=Trust_008 Trust_010"
check_plan "mint Trust_11 (max is decimal 10, not octal 8)" "Trust_11  (existing max: Trust_10)"

# A stray prefix such as net-1-old/ is not a net (it would otherwise read as a second net-1).
run_case "stray net-like prefix ignored (N=1)" 1 "net-1 net-1-old state" \
    "net-1=Trust_1"
check_contains "nets line lists net-1 only" 1 "     nets          net-1
"

# --- Mint scenarios (YES=1, past the confirmation prompt) -----------------------------

# Full run: workspaces pre-seeded, mint + upload mocked. The required startup files go
# in each kit's last upload, so flip-api never counts a half-uploaded kit.
seed_workspace net-1
seed_workspace net-2
YES_MODE=1
run_case "full mint run (N=1)" 1 "net-1 net-2" \
    "net-1=Trust_1 Trust_2 fl-server-net-1" "net-2=Trust_1 Trust_2 fl-server-net-2"
check_contains "run succeeds and reports the mint" 0 "Minted Trust_3"
check_last_upload "net-1: required startup files uploaded last" net-1/services/Trust_3
check_last_upload "net-2: required startup files uploaded last" net-2/services/Trust_3

# RECOVERY GUIDANCE ON GUARD ABORT: net-1's kit mints + uploads, then the never-overwrite
# guard trips on net-2. The abort must still print what was uploaded and the recovery —
# a guard `exit 1` bypasses the ERR trap, so this asserts the die() path keeps the
# guidance on exactly the partial-run paths.
seed_workspace net-1
seed_workspace net-2
KEYCOUNT_OVERRIDES=("net-2 Trust_3 5")
YES_MODE=1
run_case "guard abort after upload keeps recovery guidance (N=1)" 1 "net-1 net-2" \
    "net-1=Trust_1 Trust_2 fl-server-net-1" "net-2=Trust_1 Trust_2 fl-server-net-2"
check_contains "refuses net-2 overwrite" 1 "already has objects — refusing to overwrite"
check_contains "reports the net-1 upload" 1 "Kits uploaded this run (the last may be partial): net-1/Trust_3"
check_contains "says how to recover" 1 "with the number of slots still needed"

# --- ENV_NAME (FLIP#749): the environment CLASS the Makefile derives from HUB (deploy/env_mode.mk's
# --- ENV_CLASS); anything else is refused ---

ENV_MODE=stag
run_case "ENV_NAME=stag plans as the stag env (N=2)" 2 "net-1 net-2" \
    "net-1=Trust_1 Trust_2" "net-2=Trust_1 Trust_2"
check_plan "mint Trust_3 Trust_4" "Trust_3 Trust_4  (existing max: Trust_2)"
check_contains "plan header names the stag env" 1 "Plan (stag):"

ENV_MODE=prod
run_case "ENV_NAME=prod plans as the prod env (N=1)" 1 "net-1 net-2" \
    "net-1=Trust_1 Trust_2" "net-2=Trust_1 Trust_2"
check_contains "plan header names the prod env" 1 "Plan (prod):"

ENV_MODE=lza-stag
run_case "anything but prod|stag is refused (N=1)" 1 "net-1 net-2" \
    "net-1=Trust_1 Trust_2" "net-2=Trust_1 Trust_2"
check_contains "rejects ENV_NAME=lza-stag before touching anything" 1 "ENV_NAME must be"

echo ""
echo "==== ${PASS} passed, ${FAIL} failed ===="
[[ "${FAIL}" -eq 0 ]]
