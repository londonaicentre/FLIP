#!/bin/bash
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

# The Flower half of add_fl_kits.sh, which validates N and ENV_NAME and then hands over:
# mint N more *claimable* FL kit slots on a running stag/prod Flower deployment, end to
# end:
#
#   1. number past every SuperNode key in S3 and every name already minted (the
#      minted_slot_names record under fl-flower-kit-state/, operator-only: no ECS task
#      role reads that prefix), and record the new names there before anything else
#   2. mint the next supernode_credentials_<n> key pairs (via make) and upload ONLY the
#      new public keys (additive `aws s3 cp`; never `sync --delete`)
#   3. copy the public keys to the hub's EFS and register them with the RUNNING
#      SuperLink, by running the two one-shot task definitions Terraform already owns
#      (`aws ecs run-task`); nothing is replaced or restarted
#   4. upload the private keys, last
#
# flip-api's pool is every slot whose key pair is complete (FLIP#1942), and it reads S3
# at every trust registration, so the slots are claimable at the next one — no
# Terraform, no restart. A SuperNode key is not signed by the CA, so the CA, the server
# certificate and every key already handed out are untouched.
#
# Safety invariants (live trusts' keys are sacred):
#   - never writes over an existing S3 key; the emptiness check fails CLOSED
#     (an S3 error aborts the run, it is never read as "empty")
#   - never uses `upload-creds-to-s3` (`aws s3 sync --delete` over the whole net)
#   - a slot's private key is uploaded only once the SuperLink has registered its public
#     key (the task's exit code AND its log), so a slot is never claimable before its
#     SuperNode is admitted
#   - a name is recorded before its key is minted, so it is never reused: not after a
#     failed run, and not after its key was deleted from S3 to withdraw it
#   - the kit date must be the one the hub runs (flip-api's FLOWER_KITS_S3_URI): the copy
#     to the hub syncs that date's public keys onto the live EFS
#   - a mid-run failure, or an interrupt, prints exactly what was uploaded and how to recover

set -Eeuo pipefail

AICENTRE_BUCKET_NAME="${AICENTRE_BUCKET_NAME:?AICENTRE_BUCKET_NAME must be set (run via make with HUB=<env>)}"
FLOWER_KIT_DATE="${FLOWER_KIT_DATE:?FLOWER_KIT_DATE must be set (run via make with HUB=<env>)}"
YES="${YES:-}"
ECS_CLUSTER="${ECS_CLUSTER:-flip-cluster}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
FLOWER_DIR="${REPO_ROOT}/fl-services/flower"
KEY_PREFIX="fl-flower-participant-kits/${FLOWER_KIT_DATE}"
BASE_S3="s3://${AICENTRE_BUCKET_NAME}/${KEY_PREFIX}"
MINTED_KEY="fl-flower-kit-state/${FLOWER_KIT_DATE}/minted_slot_names"
MINTED_S3="s3://${AICENTRE_BUCKET_NAME}/${MINTED_KEY}"
# The one SuperLink ECS runs, and the task definitions that serve it (ecs_flower.tf,
# ecs_efs_provision.tf).
NET="net-1"
REGISTER_LOG_GROUP="/ecs/fl-server-${NET}"

log() { echo "🧩 $*"; }

# A mid-run abort must tell the operator what already reached S3. The ERR trap only
# covers command failures; guard aborts must go through die(), which routes their
# message through the same reporting (an explicit `exit 1` bypasses the trap, which
# would lose it on exactly the partial-run paths).
UPLOADED=()
on_error() {
    echo "" >&2
    echo "❌ add-fl-kits aborted." >&2
    if [[ ${#UPLOADED[@]} -gt 0 ]]; then
        echo "   Public keys uploaded this run whose slot is NOT claimable: ${UPLOADED[*]}" >&2
        echo "   Their private keys were never uploaded and are gone with this run. Delete each" >&2
        echo "   public key above, or flip-api warns about it for good:" >&2
        echo "     aws s3 rm ${BASE_S3}/${NET}/keys/supernode_credentials_<n>.pub" >&2
        echo "   Then re-run add-fl-kits with the number of slots still needed: it numbers past" >&2
        echo "   every name already minted, so these names are not reused." >&2
    else
        echo "   No key was left half-uploaded; re-run once the underlying error is fixed." >&2
    fi
}
trap on_error ERR
# An interrupt is the likeliest abort (the ECS waits run for minutes): report it too.
trap 'on_error; exit 130' INT TERM

die() {
    printf '%s\n' "$@" >&2
    on_error
    exit 1
}

# Run one of the hub's one-shot task definitions to completion, in the subnets and
# security groups of a live service, as the Terraform apply does. The task's own
# container, by name: describe-tasks also lists the GuardDuty sidecar (FLIP#1447).
run_one_shot() { # <task family> <container> <service whose network to use> [overrides JSON]
    local family="$1" container="$2" service="$3" overrides="${4:-}"
    local subnets groups public_ip run_output task_arn failure describe_output exit_code stopped_reason
    # shellcheck disable=SC2016 # the backticks are JMESPath literals, not a command
    read -r subnets groups public_ip < <(aws ecs describe-services --cluster "${ECS_CLUSTER}" --services "${service}" \
        --query 'services[0].networkConfiguration.awsvpcConfiguration.[join(`,`,subnets),join(`,`,securityGroups),assignPublicIp]' \
        --output text) || true
    [[ -n "${subnets:-}" && "${subnets}" != "None" && -n "${groups:-}" ]] ||
        die "❌ Could not read the network settings of the ${service} service — is the hub deployed?"

    # run-task exits 0 when ECS refuses to place the task (no capacity, a role it cannot
    # pass): there is then no task, only a failure reason.
    # (The calls run outside `IFS=… read`, whose IFS would also split their arguments.)
    run_output="$(aws ecs run-task --cluster "${ECS_CLUSTER}" --task-definition "${family}" \
        --launch-type FARGATE --started-by add-fl-kits \
        --network-configuration "awsvpcConfiguration={subnets=[${subnets}],securityGroups=[${groups}],assignPublicIp=${public_ip}}" \
        ${overrides:+--overrides "${overrides}"} --query '[tasks[0].taskArn, failures[0].reason]' --output text)" ||
        run_output=""
    IFS=$'\t' read -r task_arn failure <<< "${run_output}" || true
    [[ -n "${task_arn:-}" && "${task_arn}" != "None" ]] ||
        die "❌ ECS did not start the ${family} task: ${failure:-no reason given (see the AWS error above)}."
    TASK_ID="${task_arn##*/}"
    aws ecs wait tasks-stopped --cluster "${ECS_CLUSTER}" --tasks "${TASK_ID}" ||
        die "❌ The ${family} task ${TASK_ID} did not stop in time — its outcome is unknown; check its log."
    describe_output="$(aws ecs describe-tasks --cluster "${ECS_CLUSTER}" --tasks "${TASK_ID}" \
        --query "tasks[0].[containers[?name=='${container}'].exitCode | [0], stoppedReason]" --output text)" ||
        describe_output=""
    IFS=$'\t' read -r exit_code stopped_reason <<< "${describe_output}" || true
    [[ "${exit_code:-}" == "0" ]] ||
        die "❌ The ${family} task ${TASK_ID} failed (exit code '${exit_code:-unknown}': ${stopped_reason:-no reason given})" \
            "   — see its log in ${REGISTER_LOG_GROUP}."
}

# --- 1. Check the kit date, discover the net + compute the new slot names --------

# The kit date comes from the operator's env file, and the copy to the hub below syncs
# that date's public keys onto the live EFS: a date ahead of the hub (mid-rotation) would
# put a new kit's keys over the live trusts' ones, a stale one would mint into a dead
# prefix. The hub's own date is in flip-api's task definition (FLOWER_KITS_S3_URI).
flip_api_task_def="$(aws ecs describe-services --cluster "${ECS_CLUSTER}" --services flip-api \
    --query 'services[0].taskDefinition' --output text)" || {
    echo "❌ Could not read the flip-api service — refusing to mint without knowing the hub's kit date." >&2
    exit 1
}
deployed_kits_uri="$(aws ecs describe-task-definition --task-definition "${flip_api_task_def}" \
    --query "taskDefinition.containerDefinitions[].environment[] | [?name=='FLOWER_KITS_S3_URI'].value | [0]" \
    --output text)" || {
    echo "❌ Could not read ${flip_api_task_def} — refusing to mint without knowing the hub's kit date." >&2
    exit 1
}
if [[ "${deployed_kits_uri%/}" != "${BASE_S3}" ]]; then
    echo "❌ The hub serves its Flower kits from '${deployed_kits_uri}', but FLOWER_KIT_DATE gives ${BASE_S3}." >&2
    echo "   Set FLOWER_KIT_DATE to the hub's date (finish or roll back a kit rotation first)." >&2
    exit 1
fi

nets_listing="$(aws s3 ls "${BASE_S3}/")" || {
    echo "❌ Could not list ${BASE_S3}/ — see the AWS error above." >&2
    exit 1
}
nets="$(printf '%s\n' "${nets_listing}" | awk '{print $2}' | sed -n 's#^\(net-[0-9]*\)/$#\1#p' | sort -V | xargs)"
if [[ "${nets}" != "${NET}" ]]; then
    echo "❌ Expected ${NET} alone under ${BASE_S3}/, found '${nets}' — is FLOWER_KIT_DATE correct?" >&2
    echo "   The hub runs one SuperLink (${NET}), and a new key is registered with that one." >&2
    exit 1
fi
log "Net in S3 (${FLOWER_KIT_DATE}): ${NET}"

# Existing slot names: every key in S3 (either half), plus every name already minted.
# The max trailing number decides where new names start, so a name is never reused: not
# a half-uploaded one, and not one whose key was deleted from S3 to withdraw it, or was
# minted by a run that failed before its upload. Flower has no CA registry to ask (a
# SuperNode key is not signed), hence the minted_slot_names record.
keys_listing="$(aws s3 ls "${BASE_S3}/${NET}/keys/")" || {
    echo "❌ Could not list ${BASE_S3}/${NET}/keys/ — refusing to compute slot numbers" >&2
    echo "   from an incomplete view (a missed name here could reuse a live trust's slot)." >&2
    exit 1
}
existing_numbers="$(printf '%s\n' "${keys_listing}" | awk '{print $NF}' |
    sed -n 's/^supernode_credentials_\([0-9][0-9]*\)\(\.pub\)\{0,1\}$/\1/p')"

work_dir="$(mktemp -d -t add-fl-kits.XXXXXX)"
trap 'rm -rf "${work_dir}"' EXIT
minted_file="${work_dir}/minted_slot_names"
# Fails CLOSED: KeyCount comes from s3api (non-zero exit on any AWS error aborts the
# run), so a record that could not be read is never taken for "none yet".
minted_count="$(aws s3api list-objects-v2 --bucket "${AICENTRE_BUCKET_NAME}" --prefix "${MINTED_KEY}" \
    --max-keys 1 --query 'KeyCount' --output text)" || {
    echo "❌ Could not check ${MINTED_S3} — refusing to compute slot numbers without it." >&2
    exit 1
}
if [[ "${minted_count}" != "0" ]]; then
    aws s3 cp "${MINTED_S3}" "${minted_file}" --only-show-errors
else
    : > "${minted_file}"
fi

max_num=0
for number in ${existing_numbers} $(sed -n 's/^Trust_\([0-9][0-9]*\)$/\1/p' "${minted_file}"); do
    # 10# forces base 10: bash arithmetic reads a leading-zero numeral as octal.
    if ((10#${number} > max_num)); then
        max_num=$((10#${number}))
    fi
done

MINT_NUMBERS=()
MINT_NAMES=()
for ((i = 1; i <= N; i++)); do
    MINT_NUMBERS+=("$((max_num + i))")
    MINT_NAMES+=("Trust_$((max_num + i))")
done

echo ""
echo "   Plan (${ENV_NAME}):  mint ${N} kit slot(s)"
echo "     bucket        ${AICENTRE_BUCKET_NAME} (kit date ${FLOWER_KIT_DATE})"
echo "     nets          ${NET}"
echo "     mint          ${MINT_NAMES[*]}  (existing max: Trust_${max_num})"
echo ""
if [[ "${YES}" != "1" ]]; then
    read -r -p "   Proceed? [y/N] " answer
    [[ "${answer}" == "y" || "${answer}" == "Y" ]] || { echo "Aborted."; exit 1; }
fi

# --- 2. Record the names, mint, upload the public keys --------------------------

# Before anything is minted: from here on these names are spent, whatever happens.
for number in ${existing_numbers}; do echo "Trust_$((10#${number}))"; done >> "${minted_file}"
printf '%s\n' "${MINT_NAMES[@]}" >> "${minted_file}"
sort -u -V -o "${minted_file}" "${minted_file}"
aws s3 cp "${minted_file}" "${MINTED_S3}" --only-show-errors

for number in "${MINT_NUMBERS[@]}"; do
    name="Trust_${number}"
    key="supernode_credentials_${number}"
    # Refuse to touch an existing key — it may belong to a live trust. This check
    # fails CLOSED, as above.
    key_count="$(aws s3api list-objects-v2 --bucket "${AICENTRE_BUCKET_NAME}" \
        --prefix "${KEY_PREFIX}/${NET}/keys/${key}" --max-keys 1 \
        --query 'KeyCount' --output text)" ||
        die "❌ ${NET}: could not check whether ${key} already exists in S3 — refusing to upload blind."
    if [[ "${key_count}" != "0" ]]; then
        die "❌ ${NET}: ${BASE_S3}/${NET}/keys/${key} already has objects — refusing to overwrite."
    fi

    log "${NET}: minting ${name} (${key})..."
    make -C "${FLOWER_DIR}" provision-add-supernode KEY_NUMBER="${number}" OUT_DIR="${work_dir}"
    for f in "${key}" "${key}.pub"; do
        [[ -f "${work_dir}/keys/${f}" ]] ||
            die "❌ ${NET}: the mint left no keys/${f}, so flip-api would never count ${name} complete."
    done

    log "${NET}: uploading ${name}'s public key (additive)..."
    UPLOADED+=("${NET}/${key}.pub")
    aws s3 cp "${work_dir}/keys/${key}.pub" "${BASE_S3}/${NET}/keys/${key}.pub" --only-show-errors
done

# --- 3. Copy the public keys to the hub and register them with the SuperLink ----

# The EFS provisioning task, with the key sync alone as its command: additive, where
# the task's own command wipes and re-syncs the certificates a running SuperLink holds.
log "${NET}: copying the public keys to the hub (efs-provision-certs task)..."
sync_command="aws s3 sync ${BASE_S3}/${NET}/keys/ /mnt/flower-keys/ --exclude '*' --include '*.pub'"
run_one_shot efs-provision-certs provision-efs-certs "fl-server-${NET}" \
    "{\"containerOverrides\":[{\"name\":\"provision-efs-certs\",\"command\":[\"${sync_command}\"]}]}"

# The registration task as Terraform defines it: it registers every key on EFS (the
# ones already registered are skipped) and maps the node ids on the fl-api.
log "${NET}: registering the keys with the running SuperLink (flower-register-supernode-keys task)..."
run_one_shot flower-register-supernode-keys register-supernode-keys "fl-api-${NET}"

# The exit code of a one-off Fargate task has been seen to read 0 for a failed container,
# so the private keys below also wait on the task's own account of what it registered.
register_log=""
log_error=""
for attempt in 1 2 3 4 5 6; do
    if register_log="$(aws logs get-log-events --log-group-name "${REGISTER_LOG_GROUP}" \
        --log-stream-name "register-supernode-keys/register-supernode-keys/${TASK_ID}" \
        --start-from-head --query 'events[].message' --output text 2>"${work_dir}/logs.err")"; then
        log_error=""
    else
        register_log="" log_error="$(cat "${work_dir}/logs.err")"
    fi
    [[ "${register_log}" == *"All SuperNode keys registered successfully."* ]] && break
    [[ "${attempt}" == 6 ]] || sleep "${LOG_POLL_SECONDS:-5}"
done
[[ -z "${log_error}" ]] ||
    die "❌ Could not read the registration task's log (${REGISTER_LOG_GROUP}): ${log_error}" \
        "   The keys may well be registered, but without the log this run cannot tell, so no slot is released."
[[ "${register_log}" == *"All SuperNode keys registered successfully."* ]] ||
    die "❌ The registration task ${TASK_ID} did not report every key registered — see its log in ${REGISTER_LOG_GROUP}."
# A key minted by this run is new to the SuperLink: it must have been registered now, with a
# node id, not skipped or merely seen.
for number in "${MINT_NUMBERS[@]}"; do
    [[ "${register_log}" == *"OK: supernode_credentials_${number}.pub node_id="* ]] ||
        die "❌ The registration task ${TASK_ID} did not register supernode_credentials_${number}.pub" \
            "   (the copy to the hub did not deliver it, or the SuperLink did not take it) — see its log in ${REGISTER_LOG_GROUP}."
done

# --- 4. Private keys last: only now does flip-api count the slots complete ------

for number in "${MINT_NUMBERS[@]}"; do
    key="supernode_credentials_${number}"
    log "${NET}: uploading Trust_${number}'s private key (the slot becomes claimable)..."
    aws s3 cp "${work_dir}/keys/${key}" "${BASE_S3}/${NET}/keys/${key}" --only-show-errors
    # Done with this slot: drop it from the abort report.
    UPLOADED=("${UPLOADED[@]:1}")
done

echo ""
log "Minted ${MINT_NAMES[*]}: claimable at the next trust registration — no Terraform apply, no restart."
