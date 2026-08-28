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

# Black-box tests for scripts/checkov_lint.sh — the harness's OWN self-guards: the
# version-pin assertion, the unknown-check-ID validation, the mandatory skip rationale
# (both HCL comment styles, .terraform/ excluded) and the canary must-fail assertion,
# plus the real-scan exit code propagating.
#
# The canary fixture proves the real scan can fail on every CI run; nothing exercised
# these guards until now — they were probe-verified by hand for FLIP#1052. Drives the
# REAL script with `checkov` stubbed on PATH (no install, no network) inside a throwaway
# repo skeleton: the script derives AWS_ROOT and the canary path from its own location,
# so the skeleton is what gets scanned and per-case .tf fixtures never touch the
# checkout. The stub answers the three calls the script makes — `--version`, `--list`
# and `-d <dir> ...` — driven by MOCK_* variables per case.
#
# Usage:
#     bash deploy/providers/AWS/scripts/tests/test_checkov_lint.sh

set -u

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_SRC="${TESTS_DIR}/../checkov_lint.sh"
CANARY_SRC="${TESTS_DIR}/checkov_canary/main.tf"

TEST_ROOT="$(mktemp -d)"
trap 'rm -rf "${TEST_ROOT}"' EXIT

# Repo skeleton: AWS_ROOT = the copied script's grandparent, the canary at the path the
# script expects beneath it. Per-case fixtures are written under FAKE_AWS_ROOT.
FAKE_AWS_ROOT="${TEST_ROOT}/repo/deploy/providers/AWS"
mkdir -p "${FAKE_AWS_ROOT}/scripts/tests/checkov_canary"
cp "${SCRIPT_SRC}" "${FAKE_AWS_ROOT}/scripts/checkov_lint.sh"
cp "${CANARY_SRC}" "${FAKE_AWS_ROOT}/scripts/tests/checkov_canary/main.tf"
SCRIPT="${FAKE_AWS_ROOT}/scripts/checkov_lint.sh"

# Read the pin and the promoted list out of the script under test, so the stub's
# "known IDs" can never drift from what the script actually validates.
PINNED_VERSION="$(sed -n 's/^CHECKOV_VERSION="\(.*\)"$/\1/p' "${SCRIPT_SRC}")"
PROMOTED_IDS="$(grep -E '^(IAM_DOCUMENT_CHECKS|IAM_RESOURCE_CHECKS|POSTURE_CHECKS)="' "${SCRIPT_SRC}" \
    | sed -E 's/^[A-Z_]+="([^"]*)"$/\1/' | tr ',' '\n' | tr '\n' ' ')"
if [[ -z "${PINNED_VERSION}" || "${PROMOTED_IDS}" != *CKV_AWS_356* || "${PROMOTED_IDS}" != *CKV_AWS_79* ]]; then
    echo "ERROR: could not read CHECKOV_VERSION / the promoted check lists from ${SCRIPT_SRC}" >&2
    exit 1
fi

MOCKBIN="${TEST_ROOT}/mockbin"
mkdir -p "${MOCKBIN}"

# Mock checkov. MOCK_VERSION: what --version prints. MOCK_KNOWN_IDS: one --list line
# each. MOCK_CANARY_OUTPUT (if set, even empty): the canary scan's output instead of
# the default that names both must-fail IDs. MOCK_SCAN_RC: the real scan's exit code.
cat > "${MOCKBIN}/checkov" <<'MOCK_CHECKOV'
#!/usr/bin/env bash
if [[ "${1:-}" == "--version" ]]; then
    echo "${MOCK_VERSION:-3.3.14}"
    exit 0
fi
if [[ "${1:-}" == "--list" ]]; then
    for id in ${MOCK_KNOWN_IDS:-}; do echo "${id}"; done
    exit 0
fi
dir=""
while [[ $# -gt 0 ]]; do
    [[ "$1" == "-d" ]] && { dir="$2"; shift; }
    shift
done
if [[ "${dir}" == */checkov_canary ]]; then
    if [[ -n "${MOCK_CANARY_OUTPUT+set}" ]]; then
        printf '%s\n' "${MOCK_CANARY_OUTPUT}"
    else
        printf 'Check: CKV_AWS_356: canary\n\tFAILED for resource: data.aws_iam_policy_document.checkov_canary\n'
        printf 'Check: CKV_AWS_79: canary\n\tFAILED for resource: aws_instance.checkov_canary\n'
    fi
    exit 1
fi
if [[ "${MOCK_SCAN_RC:-0}" -ne 0 ]]; then
    echo "Passed checks: 112, Failed checks: 1, Skipped checks: 14"
    exit "${MOCK_SCAN_RC}"
fi
echo "Passed checks: 113, Failed checks: 0, Skipped checks: 14"
exit 0
MOCK_CHECKOV
chmod +x "${MOCKBIN}/checkov"

PASS=0
FAIL=0
LAST_OUT=""
LAST_RC=0

write_tf() {  # <path relative to AWS_ROOT> <content> — a per-case .tf fixture
    mkdir -p "$(dirname "${FAKE_AWS_ROOT}/$1")"
    printf '%s\n' "$2" > "${FAKE_AWS_ROOT}/$1"
}

run_case() {  # <name> [VAR=value ...] — run the script with the stub on PATH, then drop the fixtures
    local name="$1"
    shift
    LAST_OUT="$(env -u CHECKOV PATH="${MOCKBIN}:${PATH}" MOCK_KNOWN_IDS="${PROMOTED_IDS}" "$@" \
        bash "${SCRIPT}" 2>&1)"
    LAST_RC=$?
    rm -rf "${FAKE_AWS_ROOT}/main.tf" "${FAKE_AWS_ROOT}/.terraform"
    echo "── ${name}"
}

check_passes() {  # assert exit 0 with a substring in the output
    local label="$1" want_substr="$2"
    if [[ "${LAST_RC}" -eq 0 && "${LAST_OUT}" == *"${want_substr}"* ]]; then
        echo "   ✅ ${label}"
        PASS=$((PASS + 1))
    else
        echo "   ❌ ${label} (rc=${LAST_RC}, want 0)"
        echo "      wanted substring: ${want_substr}"
        echo "      got: ${LAST_OUT}"
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

# 1. Happy path: pinned version, every promoted ID known, no skips, canary fails both.
run_case "happy path"
check_passes "resolves the pinned version" "(version ${PINNED_VERSION})"
check_passes "canary certified, real scan green" "Canary OK"

# 2. VERSION GUARD: a PATH checkov at another version is refused ...
run_case "version drift refused" MOCK_VERSION=3.3.13
check_refuses "refuses: resolved != pinned" "resolved checkov version '3.3.13' != pinned ${PINNED_VERSION}"

# 3. ... but an explicit CHECKOV= override opts out of the assertion, as documented.
run_case "CHECKOV= override skips the version assertion" CHECKOV="${MOCKBIN}/checkov" MOCK_VERSION=3.3.13
check_passes "reports the overridden command's version" "(version 3.3.13)"
check_passes "runs to completion" "Canary OK"

# 4. UNKNOWN-ID GUARD: a promoted ID missing from --list is refused (checkov would
#    silently drop it from --check).
run_case "unknown promoted ID refused" MOCK_KNOWN_IDS="${PROMOTED_IDS/CKV_AWS_356 /}"
check_refuses "refuses: CKV_AWS_356 unknown" "promoted check CKV_AWS_356 is unknown to checkov ${PINNED_VERSION}"

# 5. UNKNOWN-ID GUARD matches whole IDs: a superstring in the list must not mask a
#    missing one (the grep -w).
run_case "superstring ID does not satisfy the guard" MOCK_KNOWN_IDS="${PROMOTED_IDS/CKV_AWS_356 /CKV_AWS_3560 }"
check_refuses "refuses: CKV_AWS_3560 is not CKV_AWS_356" "promoted check CKV_AWS_356 is unknown"

# 6. SKIP-RATIONALE GUARD: a bare `#` skip is refused and the file is named.
write_tf "main.tf" 'resource "aws_instance" "x" {
  # checkov:skip=CKV_AWS_79
}'
run_case "bare # skip refused"
check_refuses "refuses: no rationale" "checkov:skip without a rationale"
check_refuses "names the offending file" "main.tf"

# 7. SKIP-RATIONALE GUARD: the `//` comment style checkov also honours.
write_tf "main.tf" 'resource "aws_instance" "x" {
  // checkov:skip=CKV_AWS_79
}'
run_case "bare // skip refused"
check_refuses "refuses: no rationale" "checkov:skip without a rationale"

# 8. SKIP-RATIONALE GUARD: a trailing colon with nothing after it is still bare.
write_tf "main.tf" 'resource "aws_instance" "x" {
  # checkov:skip=CKV_AWS_79:
}'
run_case "skip with an empty rationale refused"
check_refuses "refuses: empty rationale" "checkov:skip without a rationale"

# 9. SKIP-RATIONALE GUARD does not over-match: a skip WITH a rationale passes.
write_tf "main.tf" 'resource "aws_instance" "x" {
  # checkov:skip=CKV_AWS_79:hop limit 2 is deliberate on the docker host
}'
run_case "skip with a rationale passes"
check_passes "passes" "Canary OK"

# 10. SKIP-RATIONALE GUARD ignores .terraform/: module code pulled by a local
#     `terraform init` carries bare skips the guard must not trip on.
write_tf ".terraform/modules/x/main.tf" 'resource "aws_instance" "x" {
  # checkov:skip=CKV_AWS_79
}'
run_case "bare skip under .terraform/ ignored"
check_passes "passes" "Canary OK"

# 11. CANARY GUARD: the canary failing only one promoted family is refused.
run_case "canary failing one family refused" MOCK_CANARY_OUTPUT="Check: CKV_AWS_356: canary
	FAILED for resource: data.aws_iam_policy_document.checkov_canary"
check_refuses "refuses: posture family did not trip" "canary fixture did not fail CKV_AWS_79"

# 12. CANARY GUARD: a vacuous (empty) canary scan is refused — the wrong-directory /
#     broken-install shape the canary exists for.
run_case "empty canary scan refused" MOCK_CANARY_OUTPUT=""
check_refuses "refuses: nothing tripped" "canary fixture did not fail CKV_AWS_356"

# 13. REAL SCAN: a failing scan propagates its exit code (set -e on the last command).
run_case "real-scan failure propagates" MOCK_SCAN_RC=1
check_refuses "canary passed first" "Canary OK"
check_refuses "run is red on the scan's findings" "Failed checks: 1"

echo ""
echo "==== ${PASS} passed, ${FAIL} failed ===="
[[ "${FAIL}" -eq 0 ]]
