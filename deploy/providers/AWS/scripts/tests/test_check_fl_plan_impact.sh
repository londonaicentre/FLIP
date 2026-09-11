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

# Black-box tests for scripts/check-fl-plan-impact.sh — the gate that decides
# whether an automated apply may proceed without an operator quiescing FL first.
#
# Drives the REAL script against synthetic `terraform show -json` documents. The
# shape of those fixtures is the contract: `resource_changes[]` entries carrying
# `.type`, `.name`, `.address` and `.change.actions`, which is what Terraform
# emits for a saved plan file.
#
# The bias under test is deliberate: every ambiguous or malformed input must fail
# closed (hold or error), never open (apply). An automated apply that guesses
# "probably fine" on a document it could not parse is the one outcome worth
# avoiding outright.
#
# Usage:
#     bash deploy/providers/AWS/scripts/tests/test_check_fl_plan_impact.sh

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$(cd "${HERE}/.." && pwd)/check-fl-plan-impact.sh"

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

# Build a plan document from "type name address action[,action]" tuples.
plan_with() {
    local out="${TEST_ROOT}/plan-$$-${RANDOM}.json"
    local entries=""
    local tuple
    for tuple in "$@"; do
        read -r rtype rname raddr ractions <<<"${tuple}"
        local actions_json
        actions_json="$(printf '%s' "${ractions}" | jq -R 'split(",")')"
        entries+="${entries:+,}$(jq -nc \
            --arg t "${rtype}" --arg n "${rname}" --arg a "${raddr}" \
            --argjson acts "${actions_json}" \
            '{type:$t, name:$n, address:$a, change:{actions:$acts}}')"
    done
    printf '{"format_version":"1.2","resource_changes":[%s]}' "${entries}" >"${out}"
    echo "${out}"
}

run_on() {
    local title="$1" file="$2"
    echo ""
    echo "-- ${title}"
    STDOUT="$(bash "${SCRIPT}" "${file}" 2>"${TEST_ROOT}/err")"
    RC=$?
    STDERR="$(cat "${TEST_ROOT}/err")"
}

expect_rc() {
    local want="$1" what="$2"
    if [[ "${RC}" -eq "${want}" ]]; then
        ok "${what} (exit ${want})"
    else
        no "${what}" "wanted exit ${want}, got ${RC}" "stdout: ${STDOUT}" "stderr: ${STDERR}"
    fi
}

expect_mentions() {
    local needle="$1" what="$2"
    if [[ "${STDOUT}${STDERR}" == *"${needle}"* ]]; then
        ok "${what}"
    else
        no "${what}" "output did not mention: ${needle}"
    fi
}

expect_silent_about() {
    local needle="$1" what="$2"
    if [[ "${STDOUT}${STDERR}" == *"${needle}"* ]]; then
        no "${what}" "output mentioned: ${needle}"
    else
        ok "${what}"
    fi
}

echo "==== check-fl-plan-impact.sh ===="

# 1. THE COMMON CASE. An infrastructure-only apply — the whole point of the gate
#    is that this proceeds unattended (decision 1: automatic release cycle).
run_on "infrastructure-only plan passes" "$(plan_with \
    "aws_s3_bucket logs aws_s3_bucket.logs update" \
    "aws_iam_role_policy ecs_flip_api_task aws_iam_role_policy.ecs_flip_api_task update" \
    "aws_ssm_parameter fl_kit_slot_names aws_ssm_parameter.fl_kit_slot_names update" \
    "aws_cloudfront_distribution flip_ui aws_cloudfront_distribution.flip_ui update")"
expect_rc 0 "safe to apply"
expect_mentions "4 resource change" "reports the change count"

# 2. An empty plan is trivially safe.
run_on "empty plan passes" "$(plan_with)"
expect_rc 0 "safe to apply"

# 3. no-op and read entries are not changes. Terraform emits them freely; counting
#    them as changes would hold on every apply.
run_on "no-op and read entries are not changes" "$(plan_with \
    "aws_ecs_service fl_server_net_1 aws_ecs_service.fl_server_net_1[0] no-op" \
    "aws_ecs_task_definition fl_api_net_1 data.aws_ecs_task_definition.fl_api_net_1[0] read")"
expect_rc 0 "safe to apply"

# 4. THE HAZARD. Replacing fl-server kills an in-flight run (FLIP#770).
run_on "fl-server replacement holds" "$(plan_with \
    "aws_ecs_service fl_server_net_1 aws_ecs_service.fl_server_net_1[0] delete,create")"
expect_rc 1 "held"
expect_mentions "aws_ecs_service.fl_server_net_1[0]" "names the resource"
expect_mentions "delete+create" "names the action"
expect_mentions "deployment mode" "points at the quiesce runbook"

# 5. Each watched address individually, so a typo in the watch list cannot pass
#    silently because a sibling entry happened to match.
for entry in \
    "aws_ecs_service fl_server_net_1" \
    "aws_ecs_service fl_api_net_1" \
    "aws_ecs_task_definition fl_server_net_1" \
    "aws_ecs_task_definition fl_api_net_1"; do
    read -r rtype rname <<<"${entry}"
    run_on "${rtype}.${rname} update holds" "$(plan_with "${rtype} ${rname} ${rtype}.${rname}[0] update")"
    expect_rc 1 "held"
done

# 6. NOT watched: a flip-api deploy. Watching it would hold nearly every apply —
#    any image-tag change updates the task definition — and the run is held by
#    fl-server, not the hub, so the hold would buy nothing.
run_on "flip-api task definition update passes" "$(plan_with \
    "aws_ecs_task_definition flip_api aws_ecs_task_definition.flip_api update" \
    "aws_ecs_service flip_api aws_ecs_service.flip_api[0] update")"
expect_rc 0 "safe to apply"

# 7. Nor the one-shot provisioning tasks.
run_on "one-shot provisioning task changes pass" "$(plan_with \
    "aws_ecs_task_definition flower_register_supernode_keys aws_ecs_task_definition.flower_register_supernode_keys update" \
    "aws_ecs_task_definition efs_provision aws_ecs_task_definition.efs_provision update")"
expect_rc 0 "safe to apply"

# 8. EFS holds FL job state — bundles, checkpoints, results staged for download.
#    An update is fine; a delete or replace destroys work no re-run recovers, so
#    that is held whether or not anything is training.
run_on "EFS update passes" "$(plan_with \
    "aws_efs_file_system flip aws_efs_file_system.flip update")"
expect_rc 0 "safe to apply"
run_on "EFS replacement holds" "$(plan_with \
    "aws_efs_file_system flip aws_efs_file_system.flip delete,create")"
expect_rc 1 "held"
expect_mentions "destroys FL job state" "explains the distinct reason"
run_on "EFS access point deletion holds" "$(plan_with \
    "aws_efs_access_point fl aws_efs_access_point.fl delete")"
expect_rc 1 "held"

# 9. A module-nested watched resource must still be caught — matching on the
#    printed address prefix would miss it, matching on type+name does not.
run_on "module-nested resource is caught" "$(plan_with \
    "aws_ecs_service fl_server_net_1 module.fl.aws_ecs_service.fl_server_net_1[0] update")"
expect_rc 1 "held"
expect_mentions "module.fl.aws_ecs_service.fl_server_net_1[0]" "reports the full module address"

# 10. FAIL CLOSED. Each of these is a way the gate could be handed something other
#     than a plan; none may be read as "nothing changes, apply away".
run_on "missing file errors" "${TEST_ROOT}/does-not-exist.json"
expect_rc 2 "errors rather than passing"
echo '{"format_version":"1.2","values":{}}' >"${TEST_ROOT}/state.json"
run_on "a state file (no resource_changes) errors" "${TEST_ROOT}/state.json"
expect_rc 2 "errors rather than passing"
expect_mentions "resource_changes" "says what was wrong"
printf 'Terraform will perform the following actions' >"${TEST_ROOT}/human.txt"
run_on "non-JSON plan output errors" "${TEST_ROOT}/human.txt"
expect_rc 2 "errors rather than passing"
printf '{"resource_changes":[{"type":"aws_ecs_service"' >"${TEST_ROOT}/truncated.json"
run_on "truncated JSON errors" "${TEST_ROOT}/truncated.json"
expect_rc 2 "errors rather than passing"

# 11. A held plan must not leak the plan body into the log — plan output for this
#     root renders secrets as (sensitive value), but the gate should not be the
#     thing that changes that.
run_on "hold message carries no plan values" "$(plan_with \
    "aws_ecs_service fl_server_net_1 aws_ecs_service.fl_server_net_1[0] delete,create")"
expect_silent_about "format_version" "prints a summary, not the plan document"

# 12. ACTIONS SURFACE. Without these a hold renders as a bare red X on the step,
#     indistinguishable from a broken pipeline — which matters because a single
#     piece of out-of-band drift makes the hold recur on every apply until one
#     quiesced apply clears it.
echo ""
echo "-- a held plan writes a job summary and an annotation"
held_plan="$(plan_with \
    "aws_ecs_service fl_server_net_1 aws_ecs_service.fl_server_net_1[0] update")"
summary="${TEST_ROOT}/summary-held.md"
: >"${summary}"
out="$(GITHUB_STEP_SUMMARY="${summary}" GITHUB_ACTIONS=true bash "${SCRIPT}" "${held_plan}" 2>&1)"
rc=$?
[[ "${rc}" -eq 1 ]] && ok "still exits 1 — a hold must not read as green" \
    || no "still exits 1" "got ${rc}"
grep -q 'Apply held' "${summary}" && ok "summary says the apply was held" \
    || no "summary says the apply was held" "$(cat "${summary}")"
grep -q 'fl_server_net_1' "${summary}" && ok "summary names the offending resource" \
    || no "summary names the offending resource"
grep -q 'fl_quiesced' "${summary}" && ok "summary carries the remedy" \
    || no "summary carries the remedy"
printf '%s' "${out}" | grep -q '::warning title=' && ok "emits a warning annotation" \
    || no "emits a warning annotation" "${out}"

echo ""
echo "-- a safe plan writes neither"
safe_plan="$(plan_with "aws_s3_bucket logs aws_s3_bucket.logs update")"
summary_safe="${TEST_ROOT}/summary-safe.md"
: >"${summary_safe}"
out="$(GITHUB_STEP_SUMMARY="${summary_safe}" GITHUB_ACTIONS=true bash "${SCRIPT}" "${safe_plan}" 2>&1)"
rc=$?
[[ "${rc}" -eq 0 ]] && ok "exits 0" || no "exits 0" "got ${rc}"
[[ ! -s "${summary_safe}" ]] && ok "writes no job summary" \
    || no "writes no job summary" "$(cat "${summary_safe}")"
printf '%s' "${out}" | grep -q '::warning' && no "emits no annotation" "${out}" \
    || ok "emits no annotation"

echo ""
echo "-- outside Actions it stays quiet on both channels"
out="$(bash "${SCRIPT}" "${held_plan}" 2>&1)" || true
printf '%s' "${out}" | grep -q '::warning' && no "no annotation without GITHUB_ACTIONS" "${out}" \
    || ok "no annotation without GITHUB_ACTIONS"

echo ""
echo "==== ${PASS} passed, ${FAIL} failed ===="
[[ "${FAIL}" -eq 0 ]]
