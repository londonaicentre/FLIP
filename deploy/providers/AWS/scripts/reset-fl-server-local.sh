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

# Empty the NVFLARE fl-server's local/ on EFS and re-sync its kit, so an older fl-server image can start
# (FLIP#1390). `make rollback-centralhub` runs it before it repoints any service.
#
# Since FLIP#1390 the fl-server writes local/flip__p_resources.json at every start. local/ is an EFS volume,
# so the file outlives the container, and NVFLARE in an fl-server image older than FLIP#1390 fails while
# loading its configuration: it cannot import the JobSchedulingReporter the file names. This runs the
# one-shot efs-provision-certs task, the one Terraform runs whenever the kit changes, which empties local/
# and re-syncs the kit from S3. The running fl-server has already loaded its configuration and is not
# affected, and a current image rewrites the file when it starts.
#
# An environment without EFS registers no efs-provision-certs task and keeps no local/ to reset; that is
# reported and is not an error. Exits non-zero when the task cannot be run or does not succeed.
#
# Usage: reset-fl-server-local.sh <ecs cluster> <fl-server ecs service>
# The aws CLI is called with the caller's AWS_PROFILE / AWS_REGION.

set -euo pipefail

cluster="${1:?Usage: reset-fl-server-local.sh <ecs cluster> <fl-server ecs service>}"
service="${2:?Usage: reset-fl-server-local.sh <ecs cluster> <fl-server ecs service>}"
family="efs-provision-certs"
# The task definition's one container (ecs_efs_provision.tf).
container="provision-efs-certs"

fail() {
    echo "❌ $1" >&2
    exit 1
}

families="$(aws ecs list-task-definition-families --family-prefix "${family}" --status ACTIVE \
    --query 'families' --output text)" || fail "Could not list the ECS task-definition families."
# --family-prefix also matches longer names; only the exact family is the provisioning task.
if ! printf '%s\n' "${families}" | tr '\t' '\n' | grep -Fxq "${family}"; then
    echo "ℹ️  No ${family} task definition: this environment keeps no fl-server local/ on EFS to reset."
    exit 0
fi

# The task runs where the fl-server does, as Terraform runs it: the fl-server's subnets and security group.
network="$(aws ecs describe-services --cluster "${cluster}" --services "${service}" \
    --query 'services[0].networkConfiguration' --output json)" || fail "Could not describe ${service}."
case "${network}" in
    "" | null) fail "${service} has no network configuration to run ${family} in." ;;
esac

echo "🧹 Emptying ${service}'s local/ on EFS and re-syncing its kit (${family} task)..."
task_arn="$(aws ecs run-task --cluster "${cluster}" --task-definition "${family}" --launch-type FARGATE \
    --network-configuration "${network}" --count 1 \
    --query 'tasks[0].taskArn' --output text)" || fail "Could not start the ${family} task."
case "${task_arn}" in
    "" | None) fail "ECS started no ${family} task." ;;
esac

aws ecs wait tasks-stopped --cluster "${cluster}" --tasks "${task_arn}" \
    || fail "The ${family} task (${task_arn}) had not stopped when the wait gave up."
# By name, never containers[0]: GuardDuty Runtime Monitoring adds a sidecar to every Fargate task, and it often
# sorts first, so index 0 can be the agent's exit code rather than the task's (AGENTS.md, TROUBLESHOOTING.md 1.9).
exit_code="$(aws ecs describe-tasks --cluster "${cluster}" --tasks "${task_arn}" \
    --query "tasks[0].containers[?name=='${container}'].exitCode | [0]" --output text)" \
    || fail "Could not read the ${family} task's exit code."
[ "${exit_code}" = "0" ] || fail "The ${family} task (${task_arn}) exited with code ${exit_code}."
echo "   ✅ local/ holds the kit again, without flip__p_resources.json."
