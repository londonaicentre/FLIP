#!/bin/bash
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
# Hard-delete a trust on the hub via a one-off ECS Fargate task. Mirrors
# register-trusts.sh's pattern: discover the running flip-api service's
# network config, run an ad-hoc task with command overrides invoking
# `flip_api.scripts.delete_trust --name <NAME>`, wait for stop, pull the
# JSON status from CloudWatch.
#
# Required env: NAME (the trust slot to delete; must match trust.name
# exactly). The Python script removes the trust row entirely and cascades
# through the nine tables holding trust.id FKs (NULL'ing the fl_kit_slot
# FK to free the slot, deleting dependent rows from the other eight) —
# see flip_api/scripts/delete_trust.py for the full contract.

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/utils.sh"

check_aws_profile

: "${ECS_CLUSTER:=flip-cluster}"
ECS_SERVICE="${ECS_SERVICE:-flip-api}"
TASK_FAMILY="${TASK_FAMILY:-flip-api}"
LOG_GROUP="${LOG_GROUP:-/ecs/flip-api}"

if [ -z "${NAME:-}" ]; then
    log_error "NAME=<trust-name> is required."
    exit 1
fi

log_info "Discovering live $ECS_SERVICE service network config..."
SVC_JSON="$(aws_cmd ecs describe-services --cluster "$ECS_CLUSTER" --services "$ECS_SERVICE" --query 'services[0]')"
SUBNETS="$(echo "$SVC_JSON" | jq -r '.networkConfiguration.awsvpcConfiguration.subnets | join(",")')"
SGS="$(echo "$SVC_JSON" | jq -r '.networkConfiguration.awsvpcConfiguration.securityGroups | join(",")')"
ASSIGN_PUB="$(echo "$SVC_JSON" | jq -r '.networkConfiguration.awsvpcConfiguration.assignPublicIp')"
if [ -z "$SUBNETS" ] || [ -z "$SGS" ]; then
    log_error "Could not read network config from $ECS_SERVICE service. Is it deployed?"
    exit 1
fi

# Wait for the service to be stable so we know the freshest task image (the
# one carrying delete_trust.py) is what our one-off task will run.
log_info "Waiting for the $ECS_SERVICE service to reach a stable state..."
aws_cmd ecs wait services-stable --cluster "$ECS_CLUSTER" --services "$ECS_SERVICE"
log_success "$ECS_SERVICE service is stable."

log_info "Hard-deleting trust '$NAME' via a one-off ECS task..."
cmd_args=(uv run python -m flip_api.scripts.delete_trust --name "$NAME")
# `--args -- ` is required: cmd_args contains dash-prefixed values (-m, --name)
# and jq scans the whole argv for options regardless of `--args`. The `--`
# end-of-options marker is what makes jq treat the rest as positional. Same
# trick register-trusts.sh uses.
overrides="$(jq -n '{containerOverrides:[{name:"flip-api",command:$ARGS.positional}]}' --args -- "${cmd_args[@]}")"

net_cfg="awsvpcConfiguration={subnets=[${SUBNETS}],securityGroups=[${SGS}],assignPublicIp=${ASSIGN_PUB}}"

task_arn="$(aws_cmd ecs run-task \
    --cluster "$ECS_CLUSTER" --task-definition "$TASK_FAMILY" --launch-type FARGATE \
    --started-by "delete-trust" \
    --network-configuration "$net_cfg" \
    --overrides "$overrides" --query 'tasks[0].taskArn' --output text)"
task_id="${task_arn##*/}"
log_info "Task $task_id launched — waiting for stop..."
aws_cmd ecs wait tasks-stopped --cluster "$ECS_CLUSTER" --tasks "$task_id"

log_info "Reading task logs..."
log_events="$(aws_cmd logs get-log-events --log-group-name "$LOG_GROUP" \
    --log-stream-name "flip-api/flip-api/$task_id" --start-from-head \
    --query 'events[].message' --output text)"
# delete_trust prints one JSON object on stdout. `aws … --output text` joins
# events[] with TABs, so split on TAB and trim before matching the leading '{'.
result_json="$(echo "$log_events" | tr '\t' '\n' | sed 's/^[[:space:]]*//' | awk 'NF' | grep -E '^\{' | tail -n 1 || true)"
if [ -z "$result_json" ]; then
    log_error "No JSON object found in task logs for $task_id — script may have crashed before printing."
    echo "$log_events"
    exit 1
fi

status="$(echo "$result_json" | jq -r '.status')"
case "$status" in
    deleted)
        freed_slot="$(echo "$result_json" | jq -r '.freed_fl_kit_slot')"
        # Compact one-line summary of which dependent tables got cleaned and how many rows.
        cascade="$(echo "$result_json" | jq -r '.dependent_rows_deleted | to_entries | map("\(.key)=\(.value)") | join(", ")')"
        log_success "Deleted trust '$NAME' → freed FL kit slot '$freed_slot' + cascaded ($cascade)."
        ;;
    not_found)
        log_warn "Trust '$NAME' not found on the hub — nothing to delete."
        ;;
    *)
        log_error "Unexpected status '$status' from delete_trust: $result_json"
        exit 1
        ;;
esac

echo "$result_json"
