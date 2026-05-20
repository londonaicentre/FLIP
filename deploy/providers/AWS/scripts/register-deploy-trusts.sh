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
# Register the trusts listed in DEPLOY_TRUSTS on the hub, then distribute the
# per-trust kit files (TRUST_API_KEY / TRUST_INTERNAL_SERVICE_KEY / FL_KIT_SLOT
# / FL_KIT_SLOT_NUMBER / EXPECTED_TRUST_ID) to the destination hosts.
#
# Mechanism:
#   1. Run `python -m flip_api.scripts.register_deploy_trusts` as a one-off ECS
#      task against the running flip-api task definition. The CLI is idempotent:
#      already-registered trusts are skipped; new registrations print a kit JSON.
#   2. Read the CLI's stdout (final JSON line) from the task's CloudWatch log stream.
#   3. For each kit, look up the destination in DEPLOY_TRUST_HOSTS:
#        "ec2"   → scp the rendered .env.<slot> to /opt/flip on the trust EC2
#                  via the existing SSM-backed `flip-trust` ssh host alias.
#        "local" → write trust/.env.<slot> in the working tree (operator host).
#   4. Re-runs produce an empty stdout JSON (`[]`) → no scp / write side-effects.

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/utils.sh"

check_aws_profile

# Inputs expected from the calling Makefile env (Makefile `include`s the env file).
: "${ECS_CLUSTER:=flip-cluster}"
ECS_SERVICE="${ECS_SERVICE:-flip-api}"
TASK_FAMILY="${TASK_FAMILY:-flip-api}"
LOG_GROUP="${LOG_GROUP:-/ecs/flip-api}"

if [ -z "${DEPLOY_TRUST_HOSTS:-}" ]; then
    log_warn "DEPLOY_TRUST_HOSTS not set; new trust kits will be printed but not distributed."
fi

# ---- Step 1: run the CLI as a one-off ECS task -----------------------------

log_info "Discovering live flip-api service network config..."
SVC_JSON="$(aws_cmd ecs describe-services \
    --cluster "$ECS_CLUSTER" \
    --services "$ECS_SERVICE" \
    --query 'services[0]')"
SUBNETS="$(echo "$SVC_JSON" | jq -r '.networkConfiguration.awsvpcConfiguration.subnets | join(",")')"
SGS="$(echo "$SVC_JSON" | jq -r '.networkConfiguration.awsvpcConfiguration.securityGroups | join(",")')"
ASSIGN_PUB="$(echo "$SVC_JSON" | jq -r '.networkConfiguration.awsvpcConfiguration.assignPublicIp')"

if [ -z "$SUBNETS" ] || [ -z "$SGS" ]; then
    log_error "Could not read network config from $ECS_SERVICE service. Is it deployed?"
    exit 1
fi

log_info "Running register_deploy_trusts as a one-off ECS task..."
OVERRIDES="$(jq -n \
    --arg cmd1 "uv" --arg cmd2 "run" --arg cmd3 "python" --arg cmd4 "-m" --arg cmd5 "flip_api.scripts.register_deploy_trusts" \
    '{containerOverrides: [{name: "flip-api", command: [$cmd1, $cmd2, $cmd3, $cmd4, $cmd5]}]}')"
TASK_ARN="$(aws_cmd ecs run-task \
    --cluster "$ECS_CLUSTER" \
    --task-definition "$TASK_FAMILY" \
    --launch-type FARGATE \
    --started-by "register-deploy-trusts" \
    --network-configuration "awsvpcConfiguration={subnets=[${SUBNETS}],securityGroups=[${SGS}],assignPublicIp=${ASSIGN_PUB}}" \
    --overrides "$OVERRIDES" \
    --query 'tasks[0].taskArn' --output text)"
TASK_ID="${TASK_ARN##*/}"
log_info "Launched task $TASK_ID; waiting for it to finish..."

aws_cmd ecs wait tasks-stopped --cluster "$ECS_CLUSTER" --tasks "$TASK_ID"

EXIT_CODE="$(aws_cmd ecs describe-tasks --cluster "$ECS_CLUSTER" --tasks "$TASK_ID" \
    --query 'tasks[0].containers[0].exitCode' --output text)"
log_info "Task $TASK_ID exited with code $EXIT_CODE."

LOG_STREAM="flip-api/flip-api/$TASK_ID"
LOG_EVENTS="$(aws_cmd logs get-log-events \
    --log-group-name "$LOG_GROUP" \
    --log-stream-name "$LOG_STREAM" \
    --start-from-head \
    --query 'events[].message' --output text)"

# The CLI prints exactly one JSON array on stdout — the last line that parses as JSON.
KITS_JSON="$(echo "$LOG_EVENTS" | awk 'NF' | tail -n 200 | grep -E '^\[' | tail -n 1 || true)"
if [ -z "$KITS_JSON" ]; then
    log_warn "No JSON kits found in the task log — assuming empty list."
    KITS_JSON="[]"
fi

if [ "$EXIT_CODE" != "0" ]; then
    log_warn "register_deploy_trusts exited non-zero — partial-failure mode. Distributing whatever kits did emit."
fi

NUM_KITS="$(echo "$KITS_JSON" | jq 'length')"
if [ "$NUM_KITS" = "0" ]; then
    log_success "No new registrations. Re-runs are no-ops by design."
    exit 0
fi

# ---- Step 2: distribute each kit to its destination host -------------------

log_info "Distributing $NUM_KITS kit(s)..."

write_kit_file() {
    local path="$1" kit="$2"
    {
        echo "# Generated by register-deploy-trusts.sh on $(date -u +'%Y-%m-%dT%H:%M:%SZ')"
        echo "# Plaintext credentials — keep this file secret. Never commit it."
        echo "TRUST_API_KEY=$(echo "$kit" | jq -r '.trust_api_key')"
        echo "TRUST_INTERNAL_SERVICE_KEY=$(echo "$kit" | jq -r '.trust_internal_service_key')"
        echo "FL_KIT_SLOT=$(echo "$kit" | jq -r '.fl_kit_slot')"
        echo "FL_KIT_SLOT_NUMBER=$(echo "$kit" | jq -r '.fl_kit_slot_number')"
        echo "EXPECTED_TRUST_ID=$(echo "$kit" | jq -r '.trust_id')"
    } > "$path"
    chmod 600 "$path"
}

echo "$KITS_JSON" | jq -c '.[]' | while read -r kit; do
    name="$(echo "$kit" | jq -r '.trust_name')"
    slot="$(echo "$kit" | jq -r '.fl_kit_slot')"
    dest="$(echo "${DEPLOY_TRUST_HOSTS:-{}}" | jq -r --arg n "$name" '.[$n] // empty')"

    if [ -z "$dest" ]; then
        log_warn "No DEPLOY_TRUST_HOSTS entry for trust ${name}; kit *not* distributed. Stash it manually:"
        echo "$kit" >&2
        continue
    fi

    case "$dest" in
        local)
            target="$REPO_ROOT/trust/.env.${slot}"
            log_info "Writing local kit for ${name} → $target"
            write_kit_file "$target" "$kit"
            log_success "Wrote $target"
            ;;
        ec2)
            tmp_kit="$(mktemp)"
            write_kit_file "$tmp_kit" "$kit"
            log_info "Copying kit for ${name} to flip-trust:/opt/flip/.env.${slot}"
            # ssh-config target `flip-trust` is set up by `make ssh-config` and uses SSM ProxyCommand.
            scp -q "$tmp_kit" "flip-trust:/opt/flip/.env.${slot}"
            rm -f "$tmp_kit"
            log_success "Distributed kit for ${name} to the trust EC2."
            ;;
        *)
            log_warn "Unknown destination '$dest' for trust ${name}; kit *not* distributed."
            ;;
    esac
done

log_success "register-deploy-trusts complete."
