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

# Drive the script as if from a laptop unless a test says otherwise. A GitHub
# runner exports GITHUB_ACTIONS and GITHUB_STEP_SUMMARY into every step, so
# inheriting them costs twice: the "outside Actions" case below cannot be
# expressed at all (the script annotates, correctly, and the assertion fails only
# in CI), and each of the eight held-plan fixtures appends a bogus "Apply held"
# block to the real job summary — the harness forging the very signal it exists
# to check. The two Actions-surface tests set both explicitly, per command.
unset GITHUB_ACTIONS GITHUB_STEP_SUMMARY

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

# 13. LZA FL INGRESS — ADVISORY ONLY (FLIP#1199). The workload side of a two-repo
#     contract: the networking account (aicentre-lza-iac) reads /flip/networking/*
#     and registers this NLB's static private IPs on the edge. Changing one side
#     without the other breaks FL ingress, so the plan must say so — but it is an
#     ordering question for a human, not a correctness failure, so the exit code
#     must not move. Holding instead would wedge every LZA ingress PR.
run_on "LZA NLB change warns but does not hold" "$(plan_with \
    "aws_lb this module.fl_server_internal_nlb.aws_lb.this[0] update")"
expect_rc 0 "exit code unchanged"
expect_mentions "LZA FL ingress" "warns about the ingress"
expect_mentions "module.fl_server_internal_nlb.aws_lb.this[0]" "names the resource"
expect_mentions "aicentre-lza-iac" "names the repo that must follow"
expect_silent_about "holding" "does not claim the apply was held"

#     An NLB replacement — the static private IPs move, which is the case that most
#     needs the networking account to go first.
run_on "LZA NLB replacement warns but does not hold" "$(plan_with \
    "aws_lb this module.fl_server_internal_nlb.aws_lb.this[0] delete,create")"
expect_rc 0 "exit code unchanged"
expect_mentions "delete+create" "names the action"

#     Each watched surface individually, so a typo in one prefix cannot pass
#     silently because a sibling entry happened to match.
for addr in \
    "module.fl_server_internal_nlb.aws_lb_listener.this[\"fl-server-tcp-listener\"]" \
    "module.fl_internal_nlb_security_group.aws_security_group.this[0]" \
    "aws_ssm_parameter.lza_fl_nlb_private_ips[0]" \
    "aws_ssm_parameter.lza_fl_port[0]" \
    "aws_ssm_parameter.lza_web_nlb_dns_name[0]" \
    "aws_ssm_parameter.lza_web_port[0]" \
    "aws_security_group_rule.ecs_fl_server_ingress_internal_nlb[0]" \
    "aws_security_group_rule.ecs_flip_api_ingress_internal_nlb[0]"; do
    run_on "${addr} warns" "$(plan_with "aws_x y ${addr} update")"
    expect_rc 0 "exit code unchanged"
    expect_mentions "LZA FL ingress" "warns"
done

#     A plan with no ingress change says nothing about it — the warning has to stay
#     rare enough to be read.
run_on "no LZA ingress change is silent" "$(plan_with \
    "aws_s3_bucket logs aws_s3_bucket.logs update" \
    "aws_ssm_parameter fl_kit_slot_names aws_ssm_parameter.fl_kit_slot_names update")"
expect_rc 0 "safe to apply"
expect_silent_about "LZA FL ingress" "no advisory"

#     LEGACY MODE UNAFFECTED. On a legacy (non-LZA) estate the NLB module, its
#     security group and the handoff parameters are all count = 0, so they never
#     appear in a plan; the legacy front door and the target groups shared between
#     the two modes must not trigger the advisory.
run_on "legacy NLB and shared target groups are silent" "$(plan_with \
    "aws_lb this module.fl_server_nlb.aws_lb.this[0] update" \
    "aws_lb_target_group ecs_fl_server_tcp aws_lb_target_group.ecs_fl_server_tcp update" \
    "aws_lb_target_group ecs_flip_api aws_lb_target_group.ecs_flip_api update")"
expect_rc 0 "safe to apply"
expect_silent_about "LZA FL ingress" "no advisory in legacy mode"

#     An ingress change alongside an FL-disruptive change: the hold still wins the
#     exit code and the hold message still appears, with the advisory beside it.
run_on "ingress change plus an FL change still holds" "$(plan_with \
    "aws_lb this module.fl_server_internal_nlb.aws_lb.this[0] update" \
    "aws_ecs_service fl_server_net_1 aws_ecs_service.fl_server_net_1[0] delete,create")"
expect_rc 1 "still held by the FL gate"
expect_mentions "would disturb FL infrastructure" "keeps the hold message"
expect_mentions "LZA FL ingress" "and carries the advisory"

#     Actions surface for the advisory: annotation + job summary, exit code 0.
echo ""
echo "-- an ingress-only plan writes an advisory summary and annotation, still exit 0"
ingress_plan="$(plan_with \
    "aws_ssm_parameter lza_fl_nlb_private_ips aws_ssm_parameter.lza_fl_nlb_private_ips[0] update")"
summary_ingress="${TEST_ROOT}/summary-ingress.md"
: >"${summary_ingress}"
out="$(GITHUB_STEP_SUMMARY="${summary_ingress}" GITHUB_ACTIONS=true bash "${SCRIPT}" "${ingress_plan}" 2>&1)"
rc=$?
[[ "${rc}" -eq 0 ]] && ok "advisory does not change the exit code" \
    || no "advisory does not change the exit code" "got ${rc}"
grep -q 'LZA FL ingress changed' "${summary_ingress}" && ok "summary carries the advisory" \
    || no "summary carries the advisory" "$(cat "${summary_ingress}")"
grep -q 'lza_fl_nlb_private_ips' "${summary_ingress}" && ok "summary names the resource" \
    || no "summary names the resource"
grep -q 'aicentre-lza-iac' "${summary_ingress}" && ok "summary names the ordering remedy" \
    || no "summary names the ordering remedy"
grep -q 'Apply held' "${summary_ingress}" && no "summary does not claim a hold" "$(cat "${summary_ingress}")" \
    || ok "summary does not claim a hold"
printf '%s' "${out}" | grep -q '::warning title=LZA FL ingress changed' && ok "emits an advisory annotation" \
    || no "emits an advisory annotation" "${out}"

echo ""
echo "-- outside Actions the advisory stays off the annotation channel"
out="$(bash "${SCRIPT}" "${ingress_plan}" 2>&1)" || true
printf '%s' "${out}" | grep -q '::warning' && no "no advisory annotation without GITHUB_ACTIONS" "${out}" \
    || ok "no advisory annotation without GITHUB_ACTIONS"
printf '%s' "${out}" | grep -q 'LZA FL ingress' && ok "but still warns on stderr" \
    || no "but still warns on stderr" "${out}"

echo ""
echo "==== ${PASS} passed, ${FAIL} failed ===="
[[ "${FAIL}" -eq 0 ]]
