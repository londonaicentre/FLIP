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
#
# Exit codes:
#     0  no watched resource changes — safe to apply unattended
#     1  a watched resource changes — hold for an operator
#     2  usage or parse error (never treated as "safe")

set -euo pipefail

die() {
    echo "❌ $*" >&2
    exit 2
}

PLAN_JSON="${1:-}"
[[ -n "${PLAN_JSON}" ]] || die "usage: $0 <plan.json>   (from: terraform show -json plan.tfplan)"
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

# Guard the input: a plan JSON with no resource_changes key is a different document
# than we think it is (a state file, a truncated download, `terraform show` without
# -json). Treating that as "nothing changes" would auto-apply on a parse failure.
if ! jq -e 'has("resource_changes")' "${PLAN_JSON}" >/dev/null 2>&1; then
    die "${PLAN_JSON} has no 'resource_changes' key — not a 'terraform show -json' plan document"
fi

ecs_hits="$(jq_hits ".resource_changes[]
    | select(${ecs_selector})
    | select(${ACTION_FILTER})
    | \"\\(.change.actions | join(\"+\"))\\t\\(.address)\"")"

efs_hits="$(jq_hits ".resource_changes[]
    | select(${efs_selector})
    | select(.change.actions | any(. == \"delete\"))
    | \"\\(.change.actions | join(\"+\"))\\t\\(.address)\"")"

total_changes="$(jq "[.resource_changes[] | select(${ACTION_FILTER})] | length" "${PLAN_JSON}")"

if [[ -z "${ecs_hits}" && -z "${efs_hits}" ]]; then
    echo "✅ No FL-disruptive resource changes in ${PLAN_JSON} (${total_changes} resource change(s) total)."
    echo "   Safe to apply without quiescing FL."
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
