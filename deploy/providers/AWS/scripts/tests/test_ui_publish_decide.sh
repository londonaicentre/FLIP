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
# Black-box tests for scripts/ui-publish-decide.sh — the rule that divides the
# flip-ui publish between its two triggers (FLIP#1186).
#
# The REAL script runs, against a synthetic repository with a real history of
# commits touching flip-ui/, deploy/providers/AWS/ and neither. What is under test
# is when a bundle ships and when it deliberately does not:
#
#   * a UI-only push publishes immediately — nothing in the deployment changed, and
#     the apply workflow will not run at all for it;
#   * a push that changed the infrastructure *and* the UI stands down, because the
#     apply those changes trigger publishes afterwards, when the API the new UI may
#     call actually exists;
#   * after an apply, only a commit that touched flip-ui/ ships — otherwise every
#     infrastructure merge would republish an unchanged bundle;
#   * an unreadable file list publishes on a push (a duplicate is harmless) and
#     does not after an apply (republishing on every merge is not);
#   * anything but develop/main is refused.
#
# Usage:
#     bash deploy/providers/AWS/scripts/tests/test_ui_publish_decide.sh

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AWS_DIR="$(cd "${HERE}/../.." && pwd)"
SCRIPT="$(cd "${HERE}/.." && pwd)/ui-publish-decide.sh"

TEST_ROOT="$(mktemp -d)"
trap 'rm -rf "${TEST_ROOT}"' EXIT

PASS=0
FAIL=0

ok() {
    echo "   ✅ $1"
    PASS=$((PASS + 1))
}

no() {
    echo "   ❌ $1"
    shift
    for line in "$@"; do echo "      ${line}"; done
    FAIL=$((FAIL + 1))
}

REPO="${TEST_ROOT}/repo"
mkdir -p "${REPO}"
git init -q "${REPO}"
git -C "${REPO}" config user.email "test@example.com"
git -C "${REPO}" config user.name "FLIP tests"
git -C "${REPO}" config commit.gpgsign false

# A history where each step changes exactly one thing, so a case can name the pair
# of commits it wants the script to compare.
commit() {
    local message="$1"
    shift
    local path
    for path in "$@"; do
        mkdir -p "$(dirname "${REPO}/${path}")"
        echo "${message}" >>"${REPO}/${path}"
        git -C "${REPO}" add "${path}"
    done
    git -C "${REPO}" commit -q -m "${message}"
    git -C "${REPO}" rev-parse HEAD
}

C0="$(commit "initial"                    README.md)"
C1="$(commit "flip-ui only"               flip-ui/src/app.ts)"
C2="$(commit "infrastructure only"        deploy/providers/AWS/main.tf)"
C3="$(commit "infrastructure and flip-ui" deploy/providers/AWS/main.tf flip-ui/src/app.ts)"
C4="$(commit "another service"            flip-api/src/main.py)"

# Runs the script the way the workflow does: from the repository root, with the
# event's payload reduced to the variables it reads.
decide() {
    local event="$1" sha="$2" before="$3" branch="$4"
    : >"${TEST_ROOT}/out"
    (
        cd "${REPO}" || exit 1
        EVENT="${event}" SHA="${sha}" BEFORE="${before}" BRANCH="${branch}" \
            bash "${SCRIPT}"
    ) >"${TEST_ROOT}/out" 2>"${TEST_ROOT}/err"
    RC=$?
    OUT="$(cat "${TEST_ROOT}/out")"
    ERR="$(cat "${TEST_ROOT}/err")"
}

field() {
    printf '%s\n' "${OUT}" | sed -n "s/^$1=//p"
}

check() {
    local title="$1" want_run="$2" want_reason="$3"
    if [[ "${RC}" -ne 0 ]]; then
        no "${title}" "rc=${RC}" "${ERR}"
        return
    fi
    if [[ "$(field run)" != "${want_run}" ]]; then
        no "${title}" "run=$(field run), wanted ${want_run}" "${OUT}"
        return
    fi
    if [[ -n "${want_reason}" && "$(field reason)" != *"${want_reason}"* ]]; then
        no "${title}" "reason=$(field reason)"
        return
    fi
    ok "${title}"
}

echo ""
echo "-- a push"

decide push "${C1}" "${C0}" develop
check "a UI-only push publishes immediately" true "flip-ui changed on a push"

decide push "${C3}" "${C2}" develop
check "a push that also changed the infrastructure stands down" false "also changed deploy/providers/AWS"

# The workflow's own path filter means this never reaches the script, but the rule
# has to stay consistent if it ever does: an infrastructure-only push is the
# apply's business, and the apply's run decides.
decide push "${C2}" "${C1}" develop
check "an infrastructure-only push does not publish either" false "also changed deploy/providers/AWS"

decide push "${C4}" "${C3}" main
check "a push that touched neither stands down (nothing to publish)" false "left flip-ui alone"

echo ""
echo "-- after the Terraform Apply workflow"

decide workflow_run "${C3}" "" develop
check "an apply of a commit that touched flip-ui publishes" true "also changed flip-ui"

decide workflow_run "${C2}" "" develop
check "an apply of an infrastructure-only commit does not" false "left flip-ui alone"

decide workflow_run "${C4}" "" main
check "an apply of an unrelated commit does not" false "left flip-ui alone"

# The FL gate holds an apply by failing it. The UI is unaffected by whether the
# deploy proceeded, so the publish still happens — this is the case where a
# `conclusion == 'success'` filter would leave the UI stale.
decide workflow_run "${C3}" "" main
check "a held apply still publishes the UI" true "also changed flip-ui"

echo ""
echo "-- an unreadable file list"

MISSING="0000000000000000000000000000000000000000"

decide push "${MISSING}" "" develop
check "a push publishes anyway, with a warning" true "could not list the changed files"

decide workflow_run "${MISSING}" "" develop
check "an apply does not republish on a guess" false "could not list the changed files"

echo ""
echo "-- the ref it checks out, and what it refuses"

decide push "${C1}" "${C0}" develop
if [[ "$(field ref)" == "${C1}" ]]; then
    ok "emits the commit under consideration as the ref to check out"
else
    no "emits the commit under consideration as the ref" "${OUT}"
fi

decide push "${C3}" "${C1}" develop
if [[ "$(field branch)" == develop && "$(field ref)" == "${C3}" ]]; then
    ok "emits the branch it decided for, so the caller does not re-derive it"
else
    no "emits the branch it decided for" "${OUT}"
fi

decide push "${C1}" "${C0}" "feature/whatever"
if [[ "${RC}" -ne 0 ]] && [[ "${ERR}" == *"develop or main"* ]]; then
    ok "refuses a branch that has no FLIP environment"
else
    no "refuses a branch that has no FLIP environment" "rc=${RC}" "${ERR}"
fi

(
    cd "${REPO}" || exit 1
    SHA="${C1}" BRANCH=develop bash "${SCRIPT}"
) >"${TEST_ROOT}/out" 2>"${TEST_ROOT}/err" && RC=0 || RC=$?
if [[ "${RC}" -ne 0 ]] && grep -q 'EVENT must be set' "${TEST_ROOT}/err"; then
    ok "refuses to guess the event it is running for"
else
    no "refuses to guess the event it is running for" "rc=${RC}" "$(cat "${TEST_ROOT}/err")"
fi

echo ""
echo "==== ${PASS} passed, ${FAIL} failed ===="
[[ "${FAIL}" -eq 0 ]]
