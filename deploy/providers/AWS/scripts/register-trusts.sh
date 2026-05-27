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
# Register each deploy trust (TRUST_1_*, TRUST_2_*, ...) on the hub and
# distribute its kit file. Per trust:
#   1. Run `flip_api.scripts.register_trust --name <name> [--code] [--region]`
#      as a one-off ECS task against the running flip-api task definition.
#      register_trust is idempotent — an already-registered trust returns a
#      metadata-only kit (no credentials; hub stores only the SHA-256 hash).
#   2. Read the kit JSON from the task's CloudWatch log stream.
#   3. Route the kit by TRUST_<n>_HOST: "ec2" → scp to the trust EC2 via the
#      SSM-backed `flip-trust` ssh alias; "local" → write trust/.env.<slot>.
# Re-runs refresh the hub-shared block in the kit file; credentials are
# preserved on the skip path (only written on first registration).

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/utils.sh"

check_aws_profile

: "${ECS_CLUSTER:=flip-cluster}"
ECS_SERVICE="${ECS_SERVICE:-flip-api}"
TASK_FAMILY="${TASK_FAMILY:-flip-api}"
LOG_GROUP="${LOG_GROUP:-/ecs/flip-api}"

# How many TRUST_<n>_* slots to consider.
TRUST_SLOT_COUNT="${TRUST_SLOT_COUNT:-2}"

log_info "Discovering live $ECS_SERVICE service network config..."
SVC_JSON="$(aws_cmd ecs describe-services --cluster "$ECS_CLUSTER" --services "$ECS_SERVICE" --query 'services[0]')"
SUBNETS="$(echo "$SVC_JSON" | jq -r '.networkConfiguration.awsvpcConfiguration.subnets | join(",")')"
SGS="$(echo "$SVC_JSON" | jq -r '.networkConfiguration.awsvpcConfiguration.securityGroups | join(",")')"
ASSIGN_PUB="$(echo "$SVC_JSON" | jq -r '.networkConfiguration.awsvpcConfiguration.assignPublicIp')"
if [ -z "$SUBNETS" ] || [ -z "$SGS" ]; then
    log_error "Could not read network config from $ECS_SERVICE service. Is it deployed?"
    exit 1
fi

# Wait for the service to be stable — its task entrypoint seeds the DB (incl. the
# FL kit-slot pool register_trust claims from); stable means that seed completed.
log_info "Waiting for the $ECS_SERVICE service to reach a stable state..."
aws_cmd ecs wait services-stable --cluster "$ECS_CLUSTER" --services "$ECS_SERVICE"
log_success "$ECS_SERVICE service is stable."

# Hub-shared keys emitted by register_trust — keep in sync with
# flip_api/scripts/register_trust.py:HUB_SHARED_ENV_KEYS and
# scripts/distribute-trust-kits.sh.
HUB_SHARED_KEYS=(
    AES_KEY_BASE64
    CENTRAL_HUB_API_URL
    TRUST_API_KEY_HEADER
    FL_BACKEND
    FLOWER_KIT_DATE
    FLARE_KIT_DATE
    DOCKER_TAG
    DOCKER_FL_TAG
    DOCKER_FL_REGISTRY
    DOCKER_FL_CLIENT_NAME
    UPLOADED_FEDERATED_DATA_BUCKET
    NLB_SUBDOMAIN
    FL_SERVER_PORT
)

# Sentinel comment that delimits the machine-managed section of each kit file.
HUB_SHARED_HEADER='# ── Hub-shared (managed by register-trust / sync-trust-kits — do not edit) ──'

write_kit_file() {
    local path="$1" kit="$2"
    # Preserve any host-local profile (LOKI_PORT, GRAFANA_PORT, OMOP_DB_PORT, …)
    # from the existing kit file, or seed it from the matching .example on first
    # run. Without this, a second register-trusts run wipes the per-trust port
    # overrides that let two dev trusts coexist on one host — both stacks then
    # fall back to the trust/Makefile defaults (LOKI_PORT=3100, …) and the
    # second `make up-trust` fails to bind. The ec2 path pre-populates the
    # tempfile by fetching the remote kit first, so credentials + hub-shared
    # are both preserved and refreshed correctly on re-runs.
    local source=""
    if [ -f "$path" ]; then
        source="$path"
    elif [ -f "${path}.example" ]; then
        source="${path}.example"
    fi

    # Build the regex for all keys that this function manages — they will be
    # stripped from the source before we re-emit them, preventing duplication.
    # Credentials are stripped only on the new-registration path (see below).
    local hub_keys_re
    hub_keys_re="$(IFS='|'; echo "${HUB_SHARED_KEYS[*]}")"

    # Determine whether this kit carries credentials (new registration) or not
    # (idempotent skip — hub stores only the SHA-256 hashes, cannot re-emit plaintext).
    local has_creds=false
    if echo "$kit" | jq -e '.trust_api_key' >/dev/null 2>&1; then
        has_creds=true
    fi

    # Build strip_re in pieces to stay within the 120-char line limit.
    # Managed keys are always stripped from the source so we can re-emit them
    # without duplication. On new-registration the credential lines are also
    # stripped (they are about to be replaced). On skip, they are left in
    # place — the hub cannot re-emit plaintext credentials.
    # EXPECTED_TRUST_ID is stripped on both paths because it is re-emitted
    # unconditionally (it is metadata, present on both new-registration and skip).
    local strip_re
    local tail_re='^# Generated by register-trusts\.sh'
    tail_re="${tail_re}|^# Plaintext credentials"
    if [ "$has_creds" = true ]; then
        strip_re='^(TRUST_API_KEY|TRUST_INTERNAL_SERVICE_KEY|EXPECTED_TRUST_ID'
        strip_re="${strip_re}|FL_KIT_SLOT|FL_KIT_SLOT_NUMBER|${hub_keys_re})="
        strip_re="${strip_re}|${tail_re}"
    else
        strip_re='^(EXPECTED_TRUST_ID|FL_KIT_SLOT|FL_KIT_SLOT_NUMBER|'"${hub_keys_re}"')='
        strip_re="${strip_re}|${tail_re}"
    fi

    # Write to a sibling tempfile, then atomically rename into place. This
    # avoids the self-clobber that occurs when source == path: opening $path
    # for output truncates it before grep can read it.
    local tmp_out
    tmp_out="$(mktemp)"
    {
        if [ -n "$source" ]; then
            grep -vE "$strip_re" "$source" | grep -vF "$HUB_SHARED_HEADER"
        fi
        echo "# Generated by register-trusts.sh on $(date -u +'%Y-%m-%dT%H:%M:%SZ')"
        echo "# Plaintext credentials — keep this file secret. Never commit it."
        # Credentials — only on new registration; skip path preserves the source lines.
        if [ "$has_creds" = true ]; then
            echo "TRUST_API_KEY=$(echo "$kit" | jq -r '.trust_api_key')"
            echo "TRUST_INTERNAL_SERVICE_KEY=$(echo "$kit" | jq -r '.trust_internal_service_key')"
        fi
        # EXPECTED_TRUST_ID, FL_KIT_SLOT, FL_KIT_SLOT_NUMBER are metadata — present on both
        # paths. Emitted unconditionally so operators who missed EXPECTED_TRUST_ID on first
        # registration can recover it via a subsequent run without re-registering.
        echo "EXPECTED_TRUST_ID=$(echo "$kit" | jq -r '.trust_id')"
        echo "FL_KIT_SLOT=$(echo "$kit" | jq -r '.fl_kit_slot')"
        echo "FL_KIT_SLOT_NUMBER=$(echo "$kit" | jq -r '.fl_kit_slot_number')"
        # Hub-shared block — present on both paths.
        echo "$HUB_SHARED_HEADER"
        echo "$kit" | jq -r '.hub_shared // {} | to_entries[] | "\(.key)=\(.value)"'
    } > "$tmp_out"
    mv "$tmp_out" "$path"
    chmod 600 "$path"
}

# Register one trust (by TRUST_<idx>_* env vars) via a one-off ECS task.
register_one() {
    local idx="$1"
    local nv cv rv hv name code region host
    nv="TRUST_${idx}_NAME"; cv="TRUST_${idx}_CODE"; rv="TRUST_${idx}_REGION"; hv="TRUST_${idx}_HOST"
    name="${!nv:-}"; code="${!cv:-}"; region="${!rv:-}"; host="${!hv:-}"
    if [ -z "$name" ]; then
        log_info "TRUST_${idx}_NAME unset — skipping slot $idx."
        return 0
    fi

    log_info "Registering trust $idx ('$name') via a one-off ECS task..."
    local cmd_args
    cmd_args=(uv run python -m flip_api.scripts.register_trust --name "$name")
    [ -n "$code" ] && cmd_args+=(--code "$code")
    [ -n "$region" ] && cmd_args+=(--region "$region")
    local overrides
    # `--args -- ` is required: cmd_args contains dash-prefixed values (`-m`, `--name`, ...)
    # and jq scans the whole argv for options regardless of `--args`. The `--` end-of-options
    # marker is what makes jq treat the rest as positional. Holds on jq 1.6 and 1.7+.
    overrides="$(jq -n '{containerOverrides:[{name:"flip-api",command:$ARGS.positional}]}' --args -- "${cmd_args[@]}")"

    local net_cfg
    net_cfg="awsvpcConfiguration={subnets=[${SUBNETS}],securityGroups=[${SGS}]"
    net_cfg="${net_cfg},assignPublicIp=${ASSIGN_PUB}}"
    local task_arn task_id
    task_arn="$(aws_cmd ecs run-task \
        --cluster "$ECS_CLUSTER" --task-definition "$TASK_FAMILY" --launch-type FARGATE \
        --started-by "register-trusts" \
        --network-configuration "$net_cfg" \
        --overrides "$overrides" --query 'tasks[0].taskArn' --output text)"
    task_id="${task_arn##*/}"
    aws_cmd ecs wait tasks-stopped --cluster "$ECS_CLUSTER" --tasks "$task_id"

    local log_events kit
    log_events="$(aws_cmd logs get-log-events --log-group-name "$LOG_GROUP" \
        --log-stream-name "flip-api/flip-api/$task_id" --start-from-head \
        --query 'events[].message' --output text)"
    # register_trust prints one JSON array on stdout — the last line starting with '['.
    # `aws ... --output text` joins the events[] list with TABs, so the kit message
    # arrives TAB-prefixed; split on TAB and trim before matching the leading '['.
    kit="$(echo "$log_events" | tr '\t' '\n' | sed 's/^[[:space:]]*//' | awk 'NF' | grep -E '^\[' | tail -n 1 || true)"
    if [ -z "$kit" ]; then
        log_warn "Trust '$name': no JSON array found in task logs — registration may have failed."
        return 0
    fi
    if [ "$(echo "$kit" | jq 'length')" = "0" ]; then
        log_warn "Trust '$name': empty kit array — registration error (non-zero exit should have caught this)."
        return 0
    fi
    kit="$(echo "$kit" | jq -c '.[0]')"

    local slot
    slot="$(echo "$kit" | jq -r '.fl_kit_slot')"
    # Detect skip path: kit present but credentials absent (hub stores only hashes).
    if ! echo "$kit" | jq -e '.trust_api_key' >/dev/null 2>&1; then
        log_info "Trust '$name' already registered — refreshing hub-shared block in trust/.env.${slot}."
    fi
    case "$host" in
        local)
            write_kit_file "$REPO_ROOT/trust/.env.${slot}" "$kit"
            log_success "Wrote kit for '$name' → trust/.env.${slot}"
            ;;
        ec2)
            local tmp_kit scp_rc
            tmp_kit="$(mktemp)"
            # Preserve existing credentials on the trust host by fetching the
            # current kit file first. On first-time registration the remote
            # file does not yet exist (scp exits 1); any other non-zero exit
            # code indicates a genuine transport error — abort rather than
            # clobber prod credentials with a credentials-less file.
            if scp -q "flip-trust:/opt/flip/trust/.env.${slot}" "$tmp_kit"; then
                :  # got the remote file; tempfile holds the current kit
            else
                scp_rc=$?
                if [ "$scp_rc" -eq 1 ]; then
                    : > "$tmp_kit"  # first-time registration; remote file absent
                else
                    log_error "scp from flip-trust failed (exit=$scp_rc) — aborting kit distribution for '$name'."
                    rm -f "$tmp_kit"
                    return 1
                fi
            fi
            write_kit_file "$tmp_kit" "$kit"
            if ! scp -q "$tmp_kit" "flip-trust:/opt/flip/trust/.env.${slot}"; then
                log_error "scp to flip-trust failed — kit was NOT installed. Tempfile $tmp_kit retained for retry."
                return 1
            fi
            rm -f "$tmp_kit"
            log_success "Distributed kit for '$name' to the trust EC2 (.env.${slot})."
            ;;
        *)
            log_warn "TRUST_${idx}_HOST='$host' unknown for trust '$name'; kit not distributed:"
            echo "$kit" >&2
            ;;
    esac
}

for idx in $(seq 1 "$TRUST_SLOT_COUNT"); do
    register_one "$idx"
done

log_success "register-trusts complete."
