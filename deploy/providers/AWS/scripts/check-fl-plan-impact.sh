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

# Decide whether a Terraform plan is safe to apply without first quiescing FL.
#
# Replacing fl-server-net-1 kills any in-flight training run (FLIP#770), which is
# why `make deploy-centralhub` prints a quiesce reminder. An automated apply has no
# operator to read that reminder, so it has to answer the question itself.
#
# The obvious pre-check — ask the hub — is not available to a GitHub runner:
# GET /fl/quiesce is gated on `verify_token` (a Cognito session, and prod runs
# ENFORCE_MFA=true), CloudFront strips X-Internal-Service-Key at the edge, and the
# scheduler state lives in Postgres inside private subnets. So instead of asking
# whether a run is in flight, this asks whether the apply could disturb one — which
# is answerable offline, from the plan itself, and is the stricter of the two
# questions when the answer is "no".
#
# Consequence: the common apply (VPC, IAM, S3, SSM, CloudFront, ALB) touches nothing
# on the watch list and proceeds unattended. Only an apply that would actually
# recreate FL infrastructure stops for a human.
#
# Usage:
#     terraform show -json plan.tfplan > plan.json
#     scripts/check-fl-plan-impact.sh plan.json
#     scripts/check-fl-plan-impact.sh --advisory-only plan.json   (or ADVISORY_ONLY=true)
#
# Exit codes:
#     0  no watched resource changes — safe to apply unattended
#     1  a watched resource changes — hold for an operator
#     2  usage or parse error (never treated as "safe")
#
# ADVISORY-ONLY MODE (--advisory-only, or ADVISORY_ONLY=true) never holds: it
# exits 0 on every readable plan, and reports what the real gate *would* do
# instead of doing it. It exists for the two places that must warn but must not
# block (FLIP#1199):
#   * terraform_plan.yml, on the pull request. This is the last point at which a
#     human can still sequence FLIP against aicentre-lza-iac, and it is the point
#     the advisory exists for — but a PR plan has no business failing a check on
#     an FL-disruptive diff, which is the apply's decision to make.
#   * terraform_apply.yml, when the gate itself is skipped by an
#     `fl_quiesced=true` re-dispatch. Quiescing FL says nothing about whether the
#     networking account has been updated, so the ingress advisory must survive
#     the attestation that silences the gate.
# A parse error is still exit 2 in this mode: an unreadable plan means the
# advisory cannot be trusted to be absent, and saying nothing would read as "no
# ingress change".
#
# Separately, and never affecting the exit code, the script emits an ADVISORY
# warning when the plan touches the LZA FL ingress (the internal NLB, its static
# private IPs, its listeners, or the /flip/networking/* handoff parameters). Those
# are half of a cross-repo contract with the networking account; see
# WATCHED_LZA_INGRESS_PREFIXES below (FLIP#1199).

set -euo pipefail

die() {
    echo "❌ $*" >&2
    exit 2
}

# ADVISORY_ONLY may arrive as an environment variable (how the workflows set it,
# beside the other `env:` keys) or as --advisory-only (how a human runs it). The
# flag wins; anything other than the literal "true" in the variable is off, so a
# mis-set value fails towards the gate rather than away from it.
ADVISORY_ONLY="${ADVISORY_ONLY:-false}"
ARGS=()
for arg in "$@"; do
    case "${arg}" in
    --advisory-only) ADVISORY_ONLY=true ;;
    -*) die "unknown option: ${arg}" ;;
    *) ARGS+=("${arg}") ;;
    esac
done
[[ "${ADVISORY_ONLY}" == "true" ]] || ADVISORY_ONLY=false

PLAN_JSON="${ARGS[0]:-}"
[[ -n "${PLAN_JSON}" ]] || die "usage: $0 [--advisory-only] <plan.json>   (from: terraform show -json plan.tfplan)"
[[ -f "${PLAN_JSON}" ]] || die "no such file: ${PLAN_JSON}"
command -v jq >/dev/null 2>&1 || die "jq is required"

# Resources whose recreation interrupts a training run in progress.
#
# Deliberately NOT on this list:
#   aws_ecs_task_definition.flip_api / aws_ecs_service.flip_api
#       A flip-api deploy is a rolling replacement (desired_count 1,
#       deployment_minimum_healthy_percent 100), and the run itself is held by
#       fl-server, not the hub. Watching it would hold nearly every apply — any
#       image-tag change updates the task definition — which would leave the
#       pipeline nominally automatic and practically manual.
#   aws_ecs_task_definition.flower_register_supernode_keys, .efs_provision
#       One-shot provisioning tasks; changing the definition does not disturb a
#       running job.
WATCHED_ECS=(
    "aws_ecs_service fl_server_net_1"
    "aws_ecs_service fl_api_net_1"
    "aws_ecs_task_definition fl_server_net_1"
    "aws_ecs_task_definition fl_api_net_1"
)

# EFS carries FL job state (uploaded bundles, checkpoints, results staged for
# download). An update is fine — a tag edit, a new access point — but a delete or
# replace destroys work that no re-run recovers, so those are held regardless of
# whether anything is training right now.
WATCHED_EFS_TYPES=(
    "aws_efs_file_system"
    "aws_efs_access_point"
    "aws_efs_mount_target"
)

# ADVISORY (never a hold). The LZA FL ingress is half of a two-repo contract: the
# workload account owns the internal NLB, its static per-subnet private IPs, its
# listeners, and the /flip/networking/* SSM parameters; the networking account
# (aicentre-lza-iac) reads those parameters and registers those IPs as targets on
# the edge NLB and the web relay. Change one side without the other and FL ingress
# breaks in a way no plan here can see — which is the failure that opened FLIP#1199.
#
# This is an ordering question for a human, not a correctness failure, so it warns
# and leaves the exit code alone. Holding would wedge every LZA ingress PR behind a
# quiesce that has nothing to do with the change.
#
# Matched by ADDRESS PREFIX, not type+name, and only on addresses that exist solely
# under `lza_managed_network`. That is what keeps legacy mode silent: in legacy the
# NLB module, its security group and the SSM handoff parameters are all `count = 0`,
# so they never appear in a plan. The shared target groups
# (aws_lb_target_group.ecs_fl_server_tcp / .ecs_flip_api) are deliberately absent —
# they are created in both modes, so watching them would warn on every legacy apply.
WATCHED_LZA_INGRESS_PREFIXES=(
    "module.fl_server_internal_nlb"
    "module.fl_internal_nlb_security_group"
    "aws_ssm_parameter.lza_"
    "aws_security_group_rule.ecs_fl_server_ingress_internal_nlb"
    "aws_security_group_rule.ecs_flip_api_ingress_internal_nlb"
)

# `no-op` and `read` are not changes. Everything else is: create, update, delete,
# and the two orderings of a replace (["delete","create"] / ["create","delete"]).
ACTION_FILTER='(.change.actions | any(. != "no-op" and . != "read"))'

# One line per hit: "<action>\t<address>". `.address` already carries the module
# prefix and any count/for_each index, so a resource inside a module reports the
# address an operator can pass straight to `terraform state show`.
jq_hits() {
    jq -r "$1" "${PLAN_JSON}"
}

ecs_selector=""
for entry in "${WATCHED_ECS[@]}"; do
    read -r wtype wname <<<"${entry}"
    ecs_selector+="${ecs_selector:+ or }(.type == \"${wtype}\" and .name == \"${wname}\")"
done

efs_selector=""
for wtype in "${WATCHED_EFS_TYPES[@]}"; do
    efs_selector+="${efs_selector:+ or }(.type == \"${wtype}\")"
done

lza_selector=""
for prefix in "${WATCHED_LZA_INGRESS_PREFIXES[@]}"; do
    lza_selector+="${lza_selector:+ or }(.address | startswith(\"${prefix}\"))"
done

# Guard the input: a plan JSON with no resource_changes key is a different document
# than we think it is (a state file, a truncated download, `terraform show` without
# -json). Treating that as "nothing changes" would auto-apply on a parse failure.
if ! jq -e 'has("resource_changes")' "${PLAN_JSON}" >/dev/null 2>&1; then
    die "${PLAN_JSON} has no 'resource_changes' key — not a 'terraform show -json' plan document"
fi

# Each jq run is guarded with `|| die` so a malformed plan document — a null
# `resource_changes`, an entry with no `change.actions` — exits 2 (the documented
# parse-error code) rather than letting `set -e` surface jq's own exit 5.
ecs_hits="$(jq_hits ".resource_changes[]
    | select(${ecs_selector})
    | select(${ACTION_FILTER})
    | \"\\(.change.actions | join(\"+\"))\\t\\(.address)\"")" || die "${PLAN_JSON} could not be read as a plan document (ECS selector)"

efs_hits="$(jq_hits ".resource_changes[]
    | select(${efs_selector})
    | select(.change.actions | any(. == \"delete\"))
    | \"\\(.change.actions | join(\"+\"))\\t\\(.address)\"")" || die "${PLAN_JSON} could not be read as a plan document (EFS selector)"

lza_hits="$(jq_hits ".resource_changes[]
    | select(${lza_selector})
    | select(${ACTION_FILTER})
    | \"\\(.change.actions | join(\"+\"))\\t\\(.address)\"")" || die "${PLAN_JSON} could not be read as a plan document (LZA ingress selector)"

total_changes="$(jq "[.resource_changes[] | select(${ACTION_FILTER})] | length" "${PLAN_JSON}")" || die "${PLAN_JSON} could not be read as a plan document (change count)"

# Advisory only — emitted on every path, never consulted for the exit code.
emit_lza_ingress_advisory() {
    [[ -n "${lza_hits}" ]] || return 0

    echo "⚠️  This plan changes the LZA FL ingress — the networking account must be updated too." >&2
    echo "" >&2
    while IFS=$'\t' read -r action address; do
        printf '     %-16s %s\n' "${action}" "${address}" >&2
    done <<<"${lza_hits}"
    cat >&2 <<'EOF'

   The edge lives in the networking account (aicentre-lza-iac): it reads
   /flip/networking/* and registers this NLB's static private IPs as targets on the
   edge NLB's FL listener and the web relay target group. Order matters:
     * ADDING or widening ingress (new IP, new subnet, new listener/port):
       apply FLIP first, then update the networking account to the new values.
     * REMOVING or replacing ingress (dropped IP/subnet, NLB replacement, port
       change): update the networking account to stop depending on the old values
       FIRST, otherwise the edge targets a resource that no longer exists.
   Confirm the handoff parameters afterwards:
     aws ssm get-parameters-by-path --path /flip/networking --recursive

   Advisory only: this does not hold any apply.
EOF

    if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
        {
            echo "### ⚠️ LZA FL ingress changed — update the networking account"
            echo
            echo "This plan touches the workload side of the cross-repo ingress contract."
            echo "**Advisory only:** this does not hold any apply."
            echo
            echo "| Action | Resource |"
            echo "| --- | --- |"
            while IFS=$'\t' read -r action address; do
                echo "| \`${action}\` | \`${address}\` |"
            done <<<"${lza_hits}"
            echo
            echo "**Order:** when *adding* ingress, apply FLIP first then update"
            echo "\`aicentre-lza-iac\`; when *removing or replacing* it, update"
            echo "\`aicentre-lza-iac\` off the old values **first**."
        } >>"${GITHUB_STEP_SUMMARY}"
    fi

    if [[ -n "${GITHUB_ACTIONS:-}" ]]; then
        echo "::warning title=LZA FL ingress changed — networking account must follow::This plan changes the internal FL NLB, its static private IPs, its listeners or the /flip/networking/* handoff parameters. The networking account (aicentre-lza-iac) edge must be updated in the right order — adding ingress: FLIP first; removing or replacing it: networking account first. Advisory only: this does not hold any apply. See the job summary."
    fi
}

if [[ -z "${ecs_hits}" && -z "${efs_hits}" ]]; then
    echo "✅ No FL-disruptive resource changes in ${PLAN_JSON} (${total_changes} resource change(s) total)."
    echo "   Safe to apply without quiescing FL."
    emit_lza_ingress_advisory
    exit 0
fi

emit_lza_ingress_advisory

# Advisory-only: report what the gate would do, and do not do it. Said on stdout
# at exit 0 so a PR plan check and an fl_quiesced re-dispatch stay green, with the
# resource addresses named so the information is not merely "something would hold".
if [[ "${ADVISORY_ONLY}" == "true" ]]; then
    echo "ℹ️  ADVISORY: this plan touches FL infrastructure (${total_changes} resource change(s) total)."
    if [[ -n "${ecs_hits}" ]]; then
        echo "   Applying it would interrupt any training run in flight:"
        while IFS=$'\t' read -r action address; do
            printf '     %-16s %s\n' "${action}" "${address}"
        done <<<"${ecs_hits}"
    fi
    if [[ -n "${efs_hits}" ]]; then
        echo "   Applying it would destroy FL job state (bundles, checkpoints, staged results):"
        while IFS=$'\t' read -r action address; do
            printf '     %-16s %s\n' "${action}" "${address}"
        done <<<"${efs_hits}"
    fi
    echo "   terraform_apply.yml will hold on merge until FL is quiesced and the apply is"
    echo "   re-dispatched with fl_quiesced=true. Nothing is held here."

    if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
        {
            echo "### ℹ️ FL infrastructure would be disturbed by applying this plan"
            echo
            echo "**Advisory only** — nothing was held here. On merge,"
            echo "\`terraform_apply.yml\` holds this plan until FL is quiesced and the apply"
            echo "is re-dispatched with \`fl_quiesced: true\`."
            echo
            echo "| Action | Resource |"
            echo "| --- | --- |"
            if [[ -n "${ecs_hits}" ]]; then while IFS=$'\t' read -r action address; do
                echo "| \`${action}\` | \`${address}\` |"
            done <<<"${ecs_hits}"; fi
            if [[ -n "${efs_hits}" ]]; then while IFS=$'\t' read -r action address; do
                echo "| \`${action}\` | \`${address}\` |"
            done <<<"${efs_hits}"; fi
        } >>"${GITHUB_STEP_SUMMARY}"
    fi

    if [[ -n "${GITHUB_ACTIONS:-}" ]]; then
        echo "::warning title=FL infrastructure would be disturbed by this plan::Applying this plan would recreate FL services or delete EFS, so terraform_apply.yml will hold it on merge until FL is quiesced and the apply is re-dispatched with fl_quiesced=true. Advisory only; nothing was held here. See the job summary."
    fi

    exit 0
fi

echo "🛑 This plan would disturb FL infrastructure — holding." >&2
echo "" >&2
[[ -n "${ecs_hits}" ]] && {
    echo "   Recreating these interrupts any training run in flight:" >&2
    while IFS=$'\t' read -r action address; do
        printf '     %-16s %s\n' "${action}" "${address}" >&2
    done <<<"${ecs_hits}"
    echo "" >&2
}
[[ -n "${efs_hits}" ]] && {
    echo "   Removing these destroys FL job state (bundles, checkpoints, staged results):" >&2
    while IFS=$'\t' read -r action address; do
        printf '     %-16s %s\n' "${action}" "${address}" >&2
    done <<<"${efs_hits}"
    echo "" >&2
}

cat >&2 <<'EOF'
   To proceed:
     1. Enable deployment mode on the hub — this pauses FL job pickup. Queued jobs
        hold; a running job finishes and frees its net.
     2. Wait until GET /fl/quiesce reports deployment mode ON and no BUSY net.
     3. Re-run terraform_apply.yml on this branch via workflow_dispatch, with the
        `fl_quiesced` input set to true. That input is what skips this gate — a
        plain re-run reads the same plan and holds again.
     4. Disable deployment mode afterwards.

   Runbook: deploy/providers/AWS/README.md, "Terraform CI: plan on PR, apply on
   merge" > "What an automated apply will not do".
EOF

# In Actions, stderr alone renders as a bare red X on the step: a hold looks
# exactly like a broken pipeline until someone opens the log. The distinction
# matters because holding is the guard working, and because a persistent piece of
# out-of-band drift makes it recur on every apply until one quiesced apply clears
# it. Still exit 1 — the apply did not happen and that must not read as green —
# but say so where it can be seen without digging.
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
    {
        echo "### 🛑 Apply held — FL infrastructure would be disturbed"
        echo
        echo "This is the guard working, not a build failure. The plan was produced"
        echo "successfully; it was **not** applied."
        echo
        echo "| Action | Resource |"
        echo "| --- | --- |"
        [[ -n "${ecs_hits}" ]] && while IFS=$'\t' read -r action address; do
            echo "| \`${action}\` | \`${address}\` |"
        done <<<"${ecs_hits}"
        [[ -n "${efs_hits}" ]] && while IFS=$'\t' read -r action address; do
            echo "| \`${action}\` | \`${address}\` |"
        done <<<"${efs_hits}"
        echo
        echo "**To proceed:** enable deployment mode on the hub, wait until"
        echo "\`GET /fl/quiesce\` reports deployment mode ON and no BUSY net, then re-run"
        echo "\`terraform_apply.yml\` via \`workflow_dispatch\` with \`fl_quiesced: true\`."
        echo "A plain re-run reads the same plan and holds again."
    } >>"${GITHUB_STEP_SUMMARY}"
fi

if [[ -n "${GITHUB_ACTIONS:-}" ]]; then
    echo "::warning title=Apply held — FL infrastructure would be disturbed::The plan succeeded but was not applied, because it would recreate FL services or delete EFS. Quiesce FL, then re-run terraform_apply.yml via workflow_dispatch with fl_quiesced set to true. See the job summary."
fi

exit 1
