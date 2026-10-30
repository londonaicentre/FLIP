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
#      trust/deploy/scripts/distribute_trust_kits.py (whatever credentials the kit carries,
#      hub-shared refreshed every run; operator host-local edits preserved).
# Delivery to a host is command-driven, NOT done here: `make -C trust/deploy/terraform/aws/ec2
# upgrade KIT=<CODE> HUB=<env>` recreates an EC2 node's containers on the new kit;
# `make package-onprem-trust-kit` tarballs it for an on-prem operator.
#
# ROTATE_KEYS=1 (set by `make rotate-trust-keys KIT=<CODE>`) runs the same CLI with
# `--rotate`: one named trust, keys reissued in place, and the kit MUST come back with
# credentials or the rotation is a failure.
#
# Usage (via `make register-trusts [KIT=<CODE>] HUB=<env>`):
#   KIT_FILES="<kit file>..." register-trusts.sh
# The deploy Makefile resolves KIT_FILES: the one kit for KIT=<CODE> (through
# deploy/env_mode.mk's kit_file, so a kit still under its pre-FLIP#1479 name is registered
# in place), else every trust/.env.*.<env> kit under its current name.
# Rotation (via `make rotate-trust-keys KIT=<CODE> HUB=<env>`) passes the same single
# kit file plus ROTATE_KEYS=1, and exactly one kit file is allowed.

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

# No kits: a warning, not an error. full-deploy runs this on a new hub, whose trusts register later.
if [ -z "${KIT_FILES:-}" ]; then
    log_warn "No trust/.env.<CODE>.${HUB:-<env>} kits found — scaffold one with 'make new-trust TRUST_CODE=<CODE> TRUST_NAME=... HUB=${HUB:-<env>}' first."
    exit 0
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

# Kit-file writing is delegated to trust/deploy/scripts/distribute_trust_kits.py (the single
# in-place-upsert writer shared with the dev path and sync). It seeds the
# host-local profile from the .example on first write, writes whatever credentials
# the kit carries (which is what makes rotation work), and upserts metadata + the
# hub-shared block under the managed sentinel — preserving operator edits on re-runs. The hub-shared key
# set and sentinel live in trust/deploy/scripts/trust_kit_lib.py.

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

    local verb="registration"
    if [ "${ROTATE_KEYS:-0}" = "1" ]; then
        verb="rotation"
        log_info "Rotating keys for trust '$name' (kit $(basename "$kit_file")) via a one-off ECS task..."
    else
        log_info "Registering trust '$name' (kit $(basename "$kit_file")) via a one-off ECS task..."
    fi
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
    if [ "${ROTATE_KEYS:-0}" = "1" ]; then
        # Rotation reissues the keys of an existing trust in place: same id, same code, same FL kit
        # slot, only api_key_hash changes. The name identifies it; the code is not needed.
        cmd_args+=(--rotate)
    else
        [ -n "$code" ] && cmd_args+=(--code "$code")
        [ -n "$region" ] && cmd_args+=(--region "$region")
    fi
    local overrides
    # `--args -- ` is required: cmd_args contains dash-prefixed values (`-m`, `--name`, ...)
    # and jq scans the whole argv for options regardless of `--args`. The `--` end-of-options
    # marker is what makes jq treat the rest as positional. Holds on jq 1.6 and 1.7+.
    #
    # No environment entries: this command override REPLACES the image's entrypoint
    # and inherits only image ENV, which is why UV_NO_SYNC / PYTHONPATH live in the
    # flip-api Dockerfile rather than in entrypoint.sh (FLIP#749: without them `uv run`
    # re-resolved the project on start and fetched setuptools from PyPI, which the
    # egress-less LZA account cannot reach — no kit ever reached SSM).
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
    # errexit is off inside this function (every call site is `... || failed=1`), so the
    # waiter's status has to be checked by hand. An expired waiter means the task is STILL
    # RUNNING: it will go on to commit the new hash and PUT the kit, so treating the waiter's
    # timeout as a failure and deleting the parameter would discard live credentials.
    if ! aws_cmd ecs wait tasks-stopped --cluster "$ECS_CLUSTER" --tasks "$task_id"; then
        log_error "Trust '$name': task $task_id did not stop within the waiter's budget — $verb status UNKNOWN."
        log_error "It may still commit. Check SSM parameter '$ssm_param' (and the task's logs) before re-running; do not assume the old key still works."
        return 1
    fi

    # Read the kit from SSM (out-of-band). The parameter is deleted only once the kit
    # file has been written: the hub cannot re-emit a kit it has already committed, so
    # deleting before the write would throw away the only copy of the new credentials.
    # We ALSO delete the CloudWatch log stream as belt-and-braces: if a future
    # code path or operator runs register_trust without --out-ssm-parameter,
    # or if a logger line accidentally captures a kit field on stderr,
    # this nukes the CloudWatch copy at the earliest opportunity.
    local kit failure_log ssm_err ssm_rc
    ssm_err="$(mktemp)"
    # `|| true` on the read would make a transient API error (throttling, creds, a 5xx)
    # indistinguishable from "the task never wrote a kit", and the absent-kit branch below
    # DELETES the parameter — which on a rotation is the only copy of the live new keys.
    # Capture the status and stderr instead, and only treat ParameterNotFound as absent.
    kit="$(aws_cmd ssm get-parameter --name "$ssm_param" --with-decryption \
        --query 'Parameter.Value' --output text 2>"$ssm_err")" && ssm_rc=0 || ssm_rc=$?
    if [ "$ssm_rc" != 0 ] && ! grep -q "ParameterNotFound" "$ssm_err"; then
        log_error "Trust '$name': could not read SSM parameter '$ssm_param' — $verb status UNKNOWN."
        cat "$ssm_err" >&2
        log_error "The parameter is left in place; it may hold the kit. Read it with 'aws ssm get-parameter --name $ssm_param --with-decryption' before re-running."
        rm -f "$ssm_err"
        return 1
    fi
    rm -f "$ssm_err"

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
        raw_log="$(aws_cmd logs get-log-events --log-group-name "$LOG_GROUP" \
            --log-stream-name "flip-api/flip-api/$task_id" --start-from-head \
            --query 'events[].message' --output text 2>/dev/null | tr '\t' '\n')"
        failure_log="$(printf '%s\n' "$raw_log" | grep -E "ERROR|WARNING" || true)"
        # A raw unhandled traceback has no log-level prefix, so the grep above
        # comes back empty; fall back to the tail of the raw log — the stream
        # is deleted just below, so this is the only evidence that survives.
        if [ -z "$failure_log" ] && [ -n "$raw_log" ]; then
            failure_log="$(printf '%s\n' "$raw_log" | tail -n 40)"
        fi
    fi

    aws_cmd logs delete-log-stream --log-group-name "$LOG_GROUP" \
        --log-stream-name "flip-api/flip-api/$task_id" >/dev/null 2>&1 || true

    if [ -z "$kit" ] || [ "$kit" = "None" ]; then
        log_error "Trust '$name': no kit in SSM parameter '$ssm_param' — $verb FAILED."
        [ -n "$failure_log" ] && printf '%s\n' "$failure_log" >&2
        # The task may have committed the new hash before its SSM write failed.
        [ "${ROTATE_KEYS:-0}" = "1" ] && log_error "The hub may already refuse the old key; re-run 'make rotate-trust-keys KIT=${KIT}'."
        aws_cmd ssm delete-parameter --name "$ssm_param" >/dev/null 2>&1 || true
        return 1
    fi
    if [ "$(echo "$kit" | jq 'length')" = "0" ]; then
        log_error "Trust '$name': empty kit array — $verb FAILED (see the task's error above)."
        [ -n "$failure_log" ] && printf '%s\n' "$failure_log" >&2
        aws_cmd ssm delete-parameter --name "$ssm_param" >/dev/null 2>&1 || true
        return 1
    fi
    kit="$(echo "$kit" | jq -c '.[0]')"
    # Credentials present / absent is the shape test. Registration is idempotent, so an
    # already-registered trust legitimately comes back metadata-only; a rotation that does
    # the same has not reissued anything and is a failure.
    if ! echo "$kit" | jq -e '.trust_api_key' >/dev/null 2>&1; then
        if [ "${ROTATE_KEYS:-0}" = "1" ]; then
            log_error "Kit for '$name' came back without credentials — rotation FAILED."
            [ -n "$failure_log" ] && printf '%s\n' "$failure_log" >&2
            aws_cmd ssm delete-parameter --name "$ssm_param" >/dev/null 2>&1 || true
            return 1
        fi
        log_info "Trust '$name' already registered — refreshing hub-shared block in $(basename "$kit_file")."
    fi

    # Write the kit back into its CODE-named file. The target is a
    # .prod/.stag kit, so distribute_trust_kits.py writes the hub-shared
    # block LIVE (the remote operator has no hub .env). Whatever credentials the
    # kit carries are written; operator host-local edits are preserved.
    #
    # errexit is disabled inside this function (every call site is `... || failed=1`),
    # so the writer's status has to be checked by hand: an unchecked failure would log
    # "Wrote kit" over a kit file that never received the new credentials. The SSM
    # parameter is left in place on that failure — it is the only remaining copy, and
    # the message below names it so the operator can recover the kit by hand.
    if ! echo "$kit" | uv run --no-config "$REPO_ROOT/trust/deploy/scripts/distribute_trust_kits.py" --target "$kit_file"; then
        log_error "Trust '$name': could not write $kit_file — $verb FAILED."
        log_error "The kit is still in SSM parameter '$ssm_param'; read it with 'aws ssm get-parameter --name $ssm_param --with-decryption', then delete it."
        return 1
    fi
    aws_cmd ssm delete-parameter --name "$ssm_param" >/dev/null 2>&1 || true
    log_success "Wrote kit for '$name' → $kit_file"
}

# `|| failed=1` so one bad kit doesn't strand the rest — every kit is attempted,
# then the script exits non-zero so a failure can never read as success.
failed=0
if [ "${ROTATE_KEYS:-0}" = "1" ]; then
    # Never rotate every trust in one go: the hub refuses each trust's old key as soon as its
    # rotation commits, so every rotated site is offline until its new kit is delivered.
    # shellcheck disable=SC2086 # deliberate word splitting, to count the kit files
    set -- ${KIT_FILES}
    if [ "$#" != 1 ]; then
        log_error "ROTATE_KEYS=1 requires KIT=<CODE> — rotation is one trust at a time."
        exit 1
    fi
fi
# shellcheck disable=SC2086 # KIT_FILES is a space-separated list of paths. Paths are
# assumed space-free: the Makefile builds the list with $(wildcard), which splits on spaces.
for kit_file in ${KIT_FILES}; do
    if [ ! -f "$kit_file" ]; then
        log_error "Kit $kit_file not found — scaffold it with 'make new-trust TRUST_CODE=${KIT} TRUST_NAME=... HUB=${HUB:-<env>}' first."
        exit 1
    fi
    register_one_kit "$kit_file" || failed=1
done

if [ "$failed" != 0 ]; then
    if [ "${ROTATE_KEYS:-0}" = "1" ]; then
        log_error "rotate-trust-keys FAILED (see above)."
    else
        log_error "register-trusts FAILED — at least one trust was not registered (see above)."
    fi
    exit 1
fi

log_success "register-trusts complete."
