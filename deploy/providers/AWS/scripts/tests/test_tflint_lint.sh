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

# Black-box tests for scripts/tflint_lint.sh — the harness's OWN self-guards: the
# version-pin assertion, the canary must-fail assertion, the canary directories being
# kept out of the real lint, and a finding in any one directory failing the run.
#
# Drives the REAL script with `tflint` stubbed on PATH (no install, no network) inside
# a throwaway repo skeleton: the script derives AWS_ROOT, the config and the canary
# path from its own location, so the skeleton is what gets linted. The stub answers the
# three calls the script makes — `--version`, `--init` and `--chdir <dir> ...` — driven
# by MOCK_* variables per case, and logs every directory it is asked to lint.
#
# Usage:
#     bash deploy/providers/AWS/scripts/tests/test_tflint_lint.sh

set -u

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_SRC="${TESTS_DIR}/../tflint_lint.sh"

TEST_ROOT="$(mktemp -d)"
trap 'rm -rf "${TEST_ROOT}"' EXIT

FAKE_AWS_ROOT="${TEST_ROOT}/repo/deploy/providers/AWS"
mkdir -p "${FAKE_AWS_ROOT}/scripts/tests/tflint_canary" "${FAKE_AWS_ROOT}/scripts/tests/checkov_canary" \
    "${FAKE_AWS_ROOT}/modules/a"
cp "${SCRIPT_SRC}" "${FAKE_AWS_ROOT}/scripts/tflint_lint.sh"
touch "${FAKE_AWS_ROOT}/.tflint.hcl" "${FAKE_AWS_ROOT}/main.tf" "${FAKE_AWS_ROOT}/modules/a/main.tf" \
    "${FAKE_AWS_ROOT}/scripts/tests/tflint_canary/main.tf" "${FAKE_AWS_ROOT}/scripts/tests/checkov_canary/main.tf"
SCRIPT="${FAKE_AWS_ROOT}/scripts/tflint_lint.sh"

PINNED_VERSION="$(sed -n 's/^TFLINT_VERSION="\(.*\)"$/\1/p' "${SCRIPT_SRC}")"
if [[ -z "${PINNED_VERSION}" ]]; then
    echo "ERROR: could not read TFLINT_VERSION from ${SCRIPT_SRC}" >&2
    exit 1
fi

MOCKBIN="${TEST_ROOT}/mockbin"
LINTED="${TEST_ROOT}/linted.log"
mkdir -p "${MOCKBIN}"

cat > "${MOCKBIN}/tflint" <<'MOCK_TFLINT'
if [[ "${1:-}" == "--version" ]]; then
    echo "TFLint version ${MOCK_VERSION:-0.64.0}"
    echo "+ ruleset.terraform (0.15.0-bundled)"
    exit 0
fi
if [[ "${1:-}" == "--init" ]]; then
    echo "All plugins are already installed"
    exit 0
fi
dir=""
while [[ $# -gt 0 ]]; do
    [[ "$1" == "--chdir" ]] && { dir="$2"; shift; }
    shift
done
echo "${dir}" >> "${MOCK_LINTED}"
if [[ "${dir}" == */tflint_canary ]]; then
    if [[ -n "${MOCK_CANARY_OUTPUT+set}" ]]; then
        printf '%s\n' "${MOCK_CANARY_OUTPUT}"
    else
        echo 'main.tf:1:1: Warning - variable "tflint_canary_unused" is declared but not used (terraform_unused_declarations)'
    fi
    exit 2
fi
if [[ -n "${MOCK_FAIL_DIR:-}" && "${dir}" == *"${MOCK_FAIL_DIR}" ]]; then
    echo 'main.tf:1:1: Warning - variable "x" is declared but not used (terraform_unused_declarations)'
    exit 2
fi
exit 0
MOCK_TFLINT
chmod +x "${MOCKBIN}/tflint"

PASS=0
FAIL=0
LAST_OUT=""
LAST_RC=0

run_case() {  # <name> [VAR=value ...] — run the script with the stub on PATH
    local name="$1"
    shift
    : > "${LINTED}"
    LAST_OUT="$(env -u TFLINT PATH="${MOCKBIN}:${PATH}" MOCK_LINTED="${LINTED}" "$@" bash "${SCRIPT}" 2>&1)"
    LAST_RC=$?
    echo "── ${name}"
}

check() {  # <label> <condition-exit-code> [detail]
    if [[ "$2" -eq 0 ]]; then
        echo "   ✅ $1"
        PASS=$((PASS + 1))
    else
        echo "   ❌ $1 (rc=${LAST_RC})"
        echo "      ${3:-}"
        echo "      got: ${LAST_OUT}"
        FAIL=$((FAIL + 1))
    fi
}

has() { [[ "${LAST_OUT}" == *"$1"* ]]; }

# 1. HAPPY PATH: pinned version, canary certified, every real directory linted.
run_case "happy path"
check "exits 0" "${LAST_RC}"
has "(version ${PINNED_VERSION})"; check "resolves the pinned version" $?
has "Canary OK"; check "canary certified" $?
grep -qx "${FAKE_AWS_ROOT}" "${LINTED}"; check "lints the root" $?
grep -qx "${FAKE_AWS_ROOT}/modules/a" "${LINTED}"; check "lints a module on its own" $?

# 2. The canaries are never part of the real lint (the tflint canary is linted
#    exactly once — as the canary; checkov's never).
run_case "canaries excluded from the real lint"
[[ "$(grep -c '/tflint_canary$' "${LINTED}")" -eq 1 ]]; check "tflint canary linted only as the canary" $?
! grep -q '/checkov_canary$' "${LINTED}"; check "checkov canary not linted" $?

# 3. VERSION PIN: drift is refused; TFLINT= overrides and skips the assertion.
run_case "version drift refused" MOCK_VERSION=0.63.0
[[ "${LAST_RC}" -ne 0 ]]; check "exits non-zero" $?
has "resolved tflint version '0.63.0' != pinned ${PINNED_VERSION}"; check "names both versions" $?

run_case "TFLINT= override skips the version assertion" TFLINT="${MOCKBIN}/tflint" MOCK_VERSION=0.63.0
check "exits 0" "${LAST_RC}"
has "(version 0.63.0)"; check "reports the overridden command's version" $?

# 4. CANARY GUARD: a canary run that flags nothing is refused before any real lint.
run_case "vacuous canary refused" MOCK_CANARY_OUTPUT=""
[[ "${LAST_RC}" -ne 0 ]]; check "exits non-zero" $?
has "canary fixture did not fail terraform_unused_declarations"; check "explains why" $?
! grep -qx "${FAKE_AWS_ROOT}" "${LINTED}"; check "real tree never linted" $?

# 5. REAL LINT: a finding in one directory fails the run, after linting the rest.
run_case "finding in a module fails the run" MOCK_FAIL_DIR=/modules/a
[[ "${LAST_RC}" -ne 0 ]]; check "exits non-zero" $?
has "tflint found issues"; check "says so" $?
grep -qx "${FAKE_AWS_ROOT}" "${LINTED}"; check "still lints the other directories" $?

echo ""
echo "==== ${PASS} passed, ${FAIL} failed ===="
[[ "${FAIL}" -eq 0 ]]
