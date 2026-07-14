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
# Register prod trust(s) on the hub and write each CODE-named kit file. Mirrors
# the dev `make register-trusts` contract — kits are trust/.env.<CODE>.<env> and
# the trust's identity is read from the kit, not from env vars. The ONLY
# difference from dev is transport: a one-off ECS Fargate task + SSM
# SecureString handoff (S-1) instead of `docker compose exec`. Per trust:
#   1. Read TRUST_NAME / TRUST_CODE / TRUST_REGION from trust/.env.<CODE>.<env>.
#   2. Run `flip_api.scripts.register_trust` as a one-off ECS task. Idempotent —
#      an already-registered trust returns a metadata-only kit (no credentials;
#      the hub stores only the SHA-256 hash), so re-running is safe.
#   3. Write the kit back into trust/.env.<CODE>.<env> via
#      scripts/distribute_trust_kits.py (credentials on first registration,
#      hub-shared refreshed every run; operator host-local edits preserved).
# Delivery to a host is command-driven, NOT done here: `make deploy-trust`
# pushes the kit to the EC2 host; `make package-onprem-trust-kit` tarballs it
# for an on-prem operator.
#
# Usage:
#   KIT=<CODE> register-trusts.sh   # register one kit (trust/.env.<CODE>.<env>)
#   register-trusts.sh              # register every live trust/.env.*.<env> kit
# PROD=true selects .production kits; anything else selects .stag.

set -eo pipefail
# Default file mode 077 so any tempfile / redirect this script (or sourced
# utils.sh) creates is owner-only by construction. The kit writer already
# chmod 600s the final kit, but a global umask defuses any earlier write
# that bypasses it — e.g. mktemp outputs, accidental `>` redirects.
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/utils.sh"

check_aws_profile

: "${ECS_CLUSTER:=flip-cluster}"
ECS_SERVICE="${ECS_SERVICE:-flip-api}"
TASK_FAMILY="${TASK_FAMILY:-flip-api}"
LOG_GROUP="${LOG_GROUP:-/ecs/flip-api}"

# Kit-file env suffix: PROD=true → .production kits, otherwise → .stag. Matches
# the env file the deploy Makefile included (.env.production / .env.stag).
ENV_SUFFIX="production"
[ "${PROD:-stag}" = "true" ] || ENV_SUFFIX="stag"

log_info "Discovering live $ECS_SERVICE service network config..."
SVC_JSON="$(aws_cmd ecs describe-services --cluster "$ECS_CLUSTER" --services "$ECS_SERVICE" --query 'services[0]')"
SUBNETS="$(echo "$SVC_JSON" | jq -r '.networkConfiguration.awsvpcConfiguration.subnets | join(",")')"
SGS="$(echo "$SVC_JSON" | jq -r '.networkConfiguration.awsvpcConfiguration.securityGroups | join(",")')"
ASSIGN_PUB="$(echo "$SVC_JSON" | jq -r '.networkConfiguration.awsvpcConfiguration.assignPublicIp')"
if [ -z "$SUBNETS" ] || [ -z "$SGS" ]; then
    log_error "Could not read network config from $ECS_SERVICE service. Is it deployed?"
    exit 1
fi

# Best-effort wait for the service to be stable — its task entrypoint seeds the
# DB (incl. the FL kit-slot pool register_trust claims from); stable means that
# seed completed. Advisory, NOT a hard gate: services-stable is a 10-minute
# waiter that fails spuriously when the service is functionally healthy but
# mid-rollout (force-redeploy, RDS-secret-rotation flap). A running task already
# implies the seed finished, so on a timeout we warn + proceed rather than abort.
log_info "Waiting for the $ECS_SERVICE service to reach a stable state..."
if aws_cmd ecs wait services-stable --cluster "$ECS_CLUSTER" --services "$ECS_SERVICE"; then
    log_success "$ECS_SERVICE service is stable."
else
    log_warn "services-stable did not complete in time — service may be mid-rollout. Proceeding anyway."
fi

# Kit-file writing is delegated to scripts/distribute_trust_kits.py (the single
# in-place-upsert writer shared with the dev path and sync). It seeds the
# host-local profile from the .example on first write, writes credentials only
# on a new registration, and upserts metadata + the hub-shared block under the
# managed sentinel — preserving operator edits on re-runs. The hub-shared key
# set and sentinel live in scripts/trust_kit_lib.py.

# Register one trust (identified by its CODE-named kit file) via a one-off ECS
# task, then write the returned kit back into that same file.
register_one_kit() {
    local kit_file="$1"
    local name code region
    name="$(sed -n 's/^TRUST_NAME=//p' "$kit_file" | head -1)"
    code="$(sed -n 's/^TRUST_CODE=//p' "$kit_file" | head -1)"
    region="$(sed -n 's/^TRUST_REGION=//p' "$kit_file" | head -1)"
    if [ -z "$name" ]; then
        log_warn "No TRUST_NAME in $kit_file — skipping."
        return 0
    fi

    log_info "Registering trust '$name' (kit $(basename "$kit_file")) via a one-off ECS task..."
    # Mint an ephemeral SSM parameter for the kit handoff (S-1). The ECS
    # task PUTs the kit JSON here as a SecureString; we GET + DELETE it
    # after the task completes. This keeps the plaintext kit out of
    # CloudWatch — only the parameter NAME ever transits stdout/awslogs,
    # which is non-sensitive (it's an ASCII identifier).
    local ssm_param uuid
    uuid="$(uuidgen 2>/dev/null || python3 -c 'import uuid;print(uuid.uuid4())')"
    ssm_param="/flip/trust-kits/ephemeral/${uuid}"

    local cmd_args
    cmd_args=(uv run python -m flip_api.scripts.register_trust --name "$name" --out-ssm-parameter "$ssm_param")
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

    # Read the kit from SSM (out-of-band), then delete the parameter. We
    # ALSO delete the CloudWatch log stream as belt-and-braces: if a future
    # code path or operator runs register_trust without --out-ssm-parameter,
    # or if a logger line accidentally captures a kit field on stderr,
    # this nukes the CloudWatch copy at the earliest opportunity.
    local kit failure_log
    kit="$(aws_cmd ssm get-parameter --name "$ssm_param" --with-decryption \
        --query 'Parameter.Value' --output text 2>/dev/null || true)"
    aws_cmd ssm delete-parameter --name "$ssm_param" >/dev/null 2>&1 || true

    # The kit (present / empty / absent) is the failure signal — NOT the task's exit
    # code, which Fargate misreports for one-off tasks (a container exiting 7 has been
    # observed as exitCode 0). register_trust writes [] on any TrustRegistrationError.
    #
    # On failure, salvage the task's logger lines BEFORE the stream is deleted below:
    # that is where the operator remediation lives (an exhausted pool names
    # apply-fl-kit-slots / add-fl-kits). Safe to echo — the kit body only ever travels
    # via --out-ssm-parameter, so the logger never carries credentials.
    failure_log=""
    if [ -z "$kit" ] || [ "$kit" = "None" ] || [ "$(echo "$kit" | jq 'length')" = "0" ]; then
        failure_log="$(aws_cmd logs get-log-events --log-group-name "$LOG_GROUP" \
            --log-stream-name "flip-api/flip-api/$task_id" --start-from-head \
            --query 'events[].message' --output text 2>/dev/null |
            tr '\t' '\n' | grep -E "ERROR|WARNING" || true)"
    fi

    aws_cmd logs delete-log-stream --log-group-name "$LOG_GROUP" \
        --log-stream-name "flip-api/flip-api/$task_id" >/dev/null 2>&1 || true

    if [ -z "$kit" ] || [ "$kit" = "None" ]; then
        log_error "Trust '$name': no kit in SSM parameter '$ssm_param' — registration FAILED."
        [ -n "$failure_log" ] && printf '%s\n' "$failure_log" >&2
        return 1
    fi
    if [ "$(echo "$kit" | jq 'length')" = "0" ]; then
        log_error "Trust '$name': empty kit array — registration FAILED (see the task's error above)."
        [ -n "$failure_log" ] && printf '%s\n' "$failure_log" >&2
        return 1
    fi
    kit="$(echo "$kit" | jq -c '.[0]')"
    # Detect skip path: kit present but credentials absent (hub stores only hashes).
    if ! echo "$kit" | jq -e '.trust_api_key' >/dev/null 2>&1; then
        log_info "Trust '$name' already registered — refreshing hub-shared block in $(basename "$kit_file")."
    fi

    # Write the kit back into its CODE-named file. The target is a
    # .production/.stag kit, so distribute_trust_kits.py writes the hub-shared
    # block LIVE (the remote operator has no hub .env). Credentials are written
    # only on first registration; operator host-local edits are preserved.
    echo "$kit" | uv run --no-config "$REPO_ROOT/scripts/distribute_trust_kits.py" --target "$kit_file"
    log_success "Wrote kit for '$name' → $kit_file"
}

# Select kits: explicit KIT=<CODE> registers one; otherwise register every live
# prod kit (trust/.env.*.<suffix>). .example templates are not matched by the
# glob (they end in .example, not the env suffix).
failed=0
if [ -n "${KIT:-}" ]; then
    kit_file="$REPO_ROOT/trust/.env.${KIT}.${ENV_SUFFIX}"
    if [ ! -f "$kit_file" ]; then
        log_error "Kit $kit_file not found — scaffold it with 'make new-trust TRUST_CODE=${KIT} TRUST_NAME=... PROD=${PROD:-stag}' first."
        exit 1
    fi
    register_one_kit "$kit_file" || failed=1
else
    found=0
    # `|| failed=1` so one bad kit doesn't strand the rest — every kit is attempted,
    # then the script exits non-zero so a failure can never read as success.
    for kit_file in "$REPO_ROOT"/trust/.env.*."${ENV_SUFFIX}"; do
        [ -e "$kit_file" ] || continue
        found=1
        register_one_kit "$kit_file" || failed=1
    done
    if [ "$found" != 1 ]; then
        log_warn "No trust/.env.*.${ENV_SUFFIX} kits found — scaffold one with 'make new-trust ... PROD=${PROD:-stag}' first."
    fi
fi

if [ "$failed" != 0 ]; then
    log_error "register-trusts FAILED — at least one trust was not registered (see above)."
    exit 1
fi

log_success "register-trusts complete."
