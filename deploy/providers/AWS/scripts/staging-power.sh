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

# Stop and start the staging estate's compute (FLIP#1596).
#
# Staging used to run permanently: three ECS services at 1/1, the hub EC2 host up
# for months, and RDS available, for an account nobody is looking at outside a
# deploy. This script is the thing that brings it down after a develop apply, its
# end to end check and a hold window, and the thing that brings it back for the
# next apply or a start dispatch.
#
# Why stop rather than destroy. A full `terraform destroy` each cycle would have
# to recreate RDS, Cognito, the KMS key and the buckets. The KMS deletion window
# alone makes that a multi day round trip, and the LZA network handoff would have
# to be renegotiated with the networking account every time. Stopping the compute
# keeps the state, the data and the handoff, and costs a few minutes to reverse.
#
# WHY THIS REFUSES PRODUCTION, LOUDLY AND IN THREE PLACES. Everything below is a
# mutating call against whatever account the ambient credentials point at, and
# the resource names are identical in both estates: `flip-cluster`,
# `flip-database`, `Ec2Instance`. A copied dispatch, a workflow edited to run on
# main, or an operator with the prod profile exported would otherwise take
# production down with no warning at all. So the guard is not a single `if`: the
# GitHub environment name, the deploy mode token (TF_PROD) and the git ref are
# each checked independently, each must be a staging value, and an unset or
# unrecognised value is a refusal rather than a pass. There is no override flag,
# because the only thing an override would ever be used for is the accident this
# exists to prevent.
#
# Usage:
#     scripts/staging-power.sh stop            scale services to 0, stop EC2, stop RDS
#     scripts/staging-power.sh start           start RDS and EC2, wait, scale services back
#     scripts/staging-power.sh start-data      RDS and EC2 only (run BEFORE a plan)
#     scripts/staging-power.sh start-services  the ECS desired counts only (run AFTER the gate)
#     scripts/staging-power.sh status          report whether staging is stopped
#     scripts/staging-power.sh hold-minutes    resolve the hold window, in minutes
#
# Environment:
#     FLIP_ENVIRONMENT   GitHub environment name (aws-stag). Required by stop/start.
#     TF_PROD            deploy/env_mode.mk token (stag | lza-stag). Required by stop/start.
#     GITHUB_REF_NAME    branch the run was loaded from. Must not be main, when set.
#     AWS_REGION         region for every call (default eu-west-2).
#     ECS_CLUSTER        default flip-cluster.
#     ECS_SERVICES       space separated, default "flip-api fl-server-net-1 fl-api-net-1".
#     FL_ECS_SERVICES    the FL subset, default "fl-server-net-1 fl-api-net-1".
#     RDS_INSTANCE_ID    default flip-database.
#     EC2_NAME_TAG       Name tag of the hub host, default Ec2Instance.
#     RUNNING_DESIRED_COUNT  what start scales each service to, default 1.
#     WAIT_SECONDS       how long start waits for RDS and EC2, default 1500.
#     DRY_RUN            true prints the mutating calls instead of making them.
#
# Exit codes:
#     0  the requested state was reached (or reported)
#     1  refused: the target is not staging, or a call failed
#     2  usage error

set -euo pipefail

SELF="$(basename "${BASH_SOURCE[0]}")"

log() { printf '%s\n' "$*"; }
warn() { printf '%s\n' "⚠️  $*" >&2; }
die() {
    printf '%s\n' "❌ $*" >&2
    exit "${2:-1}"
}
usage_die() { die "$1" 2; }

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

AWS_REGION="${AWS_REGION:-eu-west-2}"
ECS_CLUSTER="${ECS_CLUSTER:-flip-cluster}"
ECS_SERVICES="${ECS_SERVICES:-flip-api fl-server-net-1 fl-api-net-1}"
FL_ECS_SERVICES="${FL_ECS_SERVICES:-fl-server-net-1 fl-api-net-1}"
RDS_INSTANCE_ID="${RDS_INSTANCE_ID:-flip-database}"
EC2_NAME_TAG="${EC2_NAME_TAG:-Ec2Instance}"
RUNNING_DESIRED_COUNT="${RUNNING_DESIRED_COUNT:-1}"
WAIT_SECONDS="${WAIT_SECONDS:-1500}"
DRY_RUN="${DRY_RUN:-false}"
[[ "${DRY_RUN}" == "true" ]] || DRY_RUN=false

# The hold window. 120 minutes is the default from FLIP#1596: long enough to open
# the UI and chase something by hand after a deploy, short enough that forgetting
# about it costs one evening rather than one month. The ceiling is not cosmetic:
# a GitHub job is killed at six hours, so a window that cannot be slept through is
# a window that silently never stops anything.
DEFAULT_HOLD_MINUTES=120
MAX_HOLD_MINUTES=300

# Staging tokens. Membership is checked, never absence of "prod": a typo, an empty
# string and a brand new estate token must all refuse rather than pass.
STAGING_ENVIRONMENTS="aws-stag aws-staging"
STAGING_TF_PROD_TOKENS="stag lza-stag"

# ---------------------------------------------------------------------------
# Production guard
# ---------------------------------------------------------------------------

contains_word() {
    # contains_word <needle> <space separated haystack>
    local needle="$1" haystack="$2" item
    for item in ${haystack}; do
        [[ "${item}" == "${needle}" ]] && return 0
    done
    return 1
}

refuse_production() {
    local reason="$1"
    printf '%s\n' "🛑 Refusing to run '${ACTION}' against anything but staging." >&2
    printf '%s\n' "   ${reason}" >&2
    printf '%s\n' "" >&2
    cat >&2 <<EOF
   ${SELF} scales ECS services to zero and stops EC2 and RDS. The resource names
   are the same in both estates (${ECS_CLUSTER}, ${RDS_INSTANCE_ID}, ${EC2_NAME_TAG}),
   so running it with production credentials would take production down. There is
   no override: FLIP_ENVIRONMENT must be one of [${STAGING_ENVIRONMENTS}], TF_PROD
   one of [${STAGING_TF_PROD_TOKENS}], and the ref must not be main.
EOF
    if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
        {
            echo "### 🛑 Staging power action refused"
            echo
            echo "\`${SELF} ${ACTION}\` refused to run: ${reason}"
            echo
            echo "This guard exists because the staging and production resource names are"
            echo "identical, so a misrouted run would stop production."
        } >>"${GITHUB_STEP_SUMMARY}"
    fi
    exit 1
}

assert_staging_target() {
    local environment="${FLIP_ENVIRONMENT:-}"
    local tf_prod="${TF_PROD:-}"
    local ref="${GITHUB_REF_NAME:-}"

    [[ -n "${environment}" ]] ||
        refuse_production "FLIP_ENVIRONMENT is unset, so the target environment cannot be established."
    contains_word "${environment}" "${STAGING_ENVIRONMENTS}" ||
        refuse_production "FLIP_ENVIRONMENT is '${environment}', which is not a staging environment."

    [[ -n "${tf_prod}" ]] ||
        refuse_production "TF_PROD is unset, so the deploy mode cannot be established."
    contains_word "${tf_prod}" "${STAGING_TF_PROD_TOKENS}" ||
        refuse_production "TF_PROD is '${tf_prod}', which selects a production estate."

    # The ref is the third, independent check. It is allowed to be empty for a
    # laptop run, where there is no workflow ref at all, but main is never
    # acceptable: aws-prod is pinned to main, so a run from main is either a
    # production run or a workflow that is about to become one.
    if [[ -n "${ref}" && "${ref}" == "main" ]]; then
        refuse_production "the run is on 'main', which is the production branch."
    fi

    log "✅ Target confirmed as staging (environment=${environment}, TF_PROD=${tf_prod}, ref=${ref:-none})."
}

# ---------------------------------------------------------------------------
# AWS helpers
# ---------------------------------------------------------------------------

aws_ro() { aws --region "${AWS_REGION}" "$@"; }

aws_rw() {
    if [[ "${DRY_RUN}" == "true" ]]; then
        # To stderr, not stdout: every caller below is of the form
        # `if aws_rw ... >/dev/null`, so a dry-run line on stdout would be
        # swallowed by the very redirection that hides the real call's output.
        warn "DRY RUN: aws --region ${AWS_REGION} $*"
        return 0
    fi
    aws --region "${AWS_REGION}" "$@"
}

require_aws() { command -v aws >/dev/null 2>&1 || die "the AWS CLI is required"; }

# Every describe goes through here, and a describe that fails is fatal rather
# than empty.
#
# The earlier form was `aws ... 2>/dev/null` under `set -euo pipefail`. That
# combination has no good outcome: an AccessDenied (the likely one, while the
# role's permissions are being widened) discarded its own message and aborted the
# script mid-stop with an empty stderr and exit 254, and the "could not be
# described" branches that were meant to handle it could never be reached,
# because the helper never returned. So: keep the CLI's error, say which resource
# it was about, and refuse to guess the state.
#
# `die` here runs inside the caller's command substitution, so it exits that
# subshell; the non-zero status then propagates through the caller's assignment
# and `set -e` ends the run. The message is already on stderr by that point.
describe_or_die() {
    local what="$1"
    shift
    local errfile output status
    errfile="$(mktemp "${TMPDIR:-/tmp}/staging-power-describe.XXXXXX")"
    output="$("$@" 2>"${errfile}")" || status=$?
    status="${status:-0}"
    if [[ "${status}" -ne 0 ]]; then
        local detail
        detail="$(tr '\n' ' ' <"${errfile}" | sed 's/  */ /g; s/ *$//')"
        rm -f "${errfile}"
        warn "aws exited ${status} describing ${what}: ${detail:-no error output}"
        die "could not describe ${what}; refusing to act on a state that was never read."
    fi
    rm -f "${errfile}"
    printf '%s' "${output}"
}

# Desired count of one service, or the empty string when the service is absent.
# An absent service is not an error: enable_service_discovery can be off, and a
# stop that insists on three services would fail on an estate that has two. A
# describe that FAILS is a different thing entirely, and is fatal.
service_desired_count() {
    local service="$1"
    describe_or_die "the ECS service ${service}" \
        aws_ro ecs describe-services \
        --cluster "${ECS_CLUSTER}" --services "${service}" \
        --query 'services[?status!=`INACTIVE`].desiredCount' --output text |
        tr -d '[:space:]'
}

hub_instance_id() {
    describe_or_die "the EC2 instances tagged Name=${EC2_NAME_TAG}" \
        aws_ro ec2 describe-instances \
        --filters "Name=tag:Name,Values=${EC2_NAME_TAG}" \
        "Name=instance-state-name,Values=pending,running,stopping,stopped" \
        --query 'Reservations[].Instances[].InstanceId' --output text |
        tr '[:space:]' ' ' | sed 's/ *$//'
}

hub_instance_state() {
    local instance_id="$1"
    describe_or_die "the EC2 instance ${instance_id}" \
        aws_ro ec2 describe-instances --instance-ids "${instance_id}" \
        --query 'Reservations[].Instances[].State.Name' --output text |
        tr -d '[:space:]'
}

rds_status() {
    describe_or_die "the RDS instance ${RDS_INSTANCE_ID}" \
        aws_ro rds describe-db-instances --db-instance-identifier "${RDS_INSTANCE_ID}" \
        --query 'DBInstances[0].DBInstanceStatus' --output text |
        tr -d '[:space:]'
}

# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------

do_stop() {
    assert_staging_target
    require_aws
    local failures=0 service count instance_id state status

    # What actually happened, per resource, for the summary. The summary used to
    # hard-code "stopped" on every row, so a stop that left RDS up reported a
    # fully stopped estate. Each row below is now written by the branch that did
    # (or did not do) the thing.
    STOP_ROWS=()
    STOP_RDS_LEFT_UP=false

    log ""
    log ">> Scaling the ECS services to 0 on ${ECS_CLUSTER}"
    for service in ${ECS_SERVICES}; do
        count="$(service_desired_count "${service}")"
        if [[ -z "${count}" || "${count}" == "None" ]]; then
            log "   ${service}: not present, nothing to scale."
            STOP_ROWS+=("\`${service}\` (ECS)|not present")
            continue
        fi
        if [[ "${count}" == "0" ]]; then
            log "   ${service}: already at 0."
            STOP_ROWS+=("\`${service}\` (ECS)|already at desired count 0")
            continue
        fi
        if aws_rw ecs update-service --cluster "${ECS_CLUSTER}" --service "${service}" \
            --desired-count 0 >/dev/null; then
            log "   ${service}: ${count} -> 0."
            STOP_ROWS+=("\`${service}\` (ECS)|desired count ${count} → 0")
        else
            warn "${service}: update-service failed."
            STOP_ROWS+=("\`${service}\` (ECS)|❌ update-service failed, still at ${count}")
            failures=$((failures + 1))
        fi
    done

    log ""
    log ">> Stopping the hub EC2 host (tag Name=${EC2_NAME_TAG})"
    instance_id="$(hub_instance_id)"
    if [[ -z "${instance_id}" ]]; then
        log "   no instance matches, nothing to stop."
        STOP_ROWS+=("\`${EC2_NAME_TAG}\` (EC2)|no instance matches the tag")
    else
        for id in ${instance_id}; do
            state="$(hub_instance_state "${id}")"
            case "${state}" in
            stopped | stopping)
                log "   ${id}: already ${state}."
                STOP_ROWS+=("\`${id}\` (EC2)|already ${state}")
                ;;
            *)
                if aws_rw ec2 stop-instances --instance-ids "${id}" >/dev/null; then
                    log "   ${id}: ${state:-unknown} -> stopping."
                    STOP_ROWS+=("\`${id}\` (EC2)|${state:-unknown} → stopping")
                else
                    warn "${id}: stop-instances failed."
                    STOP_ROWS+=("\`${id}\` (EC2)|❌ stop-instances failed, still ${state:-unknown}")
                    failures=$((failures + 1))
                fi
                ;;
            esac
        done
    fi

    log ""
    log ">> Stopping RDS ${RDS_INSTANCE_ID}"
    status="$(rds_status)"
    case "${status}" in
    None)
        warn "${RDS_INSTANCE_ID} has no status, leaving it alone."
        STOP_ROWS+=("\`${RDS_INSTANCE_ID}\` (RDS)|❌ no status reported, left alone")
        STOP_RDS_LEFT_UP=true
        failures=$((failures + 1))
        ;;
    stopped | stopping)
        log "   already ${status}."
        STOP_ROWS+=("\`${RDS_INSTANCE_ID}\` (RDS)|already ${status}")
        ;;
    available)
        if aws_rw rds stop-db-instance --db-instance-identifier "${RDS_INSTANCE_ID}" >/dev/null; then
            log "   available -> stopping."
            STOP_ROWS+=("\`${RDS_INSTANCE_ID}\` (RDS)|available → stopping")
        else
            warn "stop-db-instance failed."
            STOP_ROWS+=("\`${RDS_INSTANCE_ID}\` (RDS)|❌ stop-db-instance failed, still available")
            STOP_RDS_LEFT_UP=true
            failures=$((failures + 1))
        fi
        ;;
    *)
        # backing-up, modifying, starting: a stop would be rejected, and retrying
        # here would hold the job for an unbounded time. The nightly guard runs
        # again in under a day and finds it settled. Not a failure, but not a
        # stopped database either, so it is reported as what it is — including as
        # an Actions warning annotation, because the cost of this case is a
        # database running until 03:30 and nobody reading the log.
        warn "${RDS_INSTANCE_ID} is '${status}', which cannot be stopped now. The nightly guard will retry."
        annotate_warning "Staging RDS left up" \
            "${RDS_INSTANCE_ID} was '${status}', which cannot be stopped. It is still running; the nightly guard retries at 03:30 UTC."
        STOP_ROWS+=("\`${RDS_INSTANCE_ID}\` (RDS)|⚠️ '${status}', cannot be stopped now — left up")
        STOP_RDS_LEFT_UP=true
        ;;
    esac

    log ""
    if [[ "${failures}" -gt 0 ]]; then
        summarise_stop
        die "${failures} stop action(s) failed; staging may still be partly up."
    fi
    if [[ "${STOP_RDS_LEFT_UP}" == "true" ]]; then
        log "⚠️  Staging stop requested, but ${RDS_INSTANCE_ID} is still up. ECS is at 0."
    else
        log "✅ Staging stop requested. ECS at 0, EC2 stopping, RDS stopping."
    fi
    summarise_stop
}

# A GitHub warning annotation, when there is a GitHub to annotate.
annotate_warning() {
    local title="$1" message="$2"
    [[ -n "${GITHUB_ACTIONS:-}" ]] || return 0
    printf '::warning title=%s::%s\n' "${title}" "${message}"
}

summarise_stop() {
    [[ -n "${GITHUB_STEP_SUMMARY:-}" ]] || return 0
    local row
    {
        # `failures` is do_stop's local; summarise_stop only runs from there.
        if [[ "${failures:-0}" -gt 0 ]]; then
            echo "### ❌ Staging stop failed: ${failures} action(s), staging may still be partly up"
        elif [[ "${STOP_RDS_LEFT_UP:-false}" == "true" ]]; then
            echo "### ⚠️ Staging partly stopped"
        else
            echo "### 🌙 Staging stopped"
        fi
        echo
        echo "| Resource | Action |"
        echo "| --- | --- |"
        for row in "${STOP_ROWS[@]+"${STOP_ROWS[@]}"}"; do
            echo "| ${row%%|*} | ${row#*|} |"
        done
        echo
        echo "Start it again with the \`start-stag\` dispatch of \`staging_lifecycle.yml\`,"
        echo "or by merging to \`develop\`, which starts it first."
    } >>"${GITHUB_STEP_SUMMARY}"
}

# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------

wait_for() {
    # wait_for <description> <ready states> <state reader...>
    #
    # The reader runs as a plain assignment, never as an `if` condition. Inside
    # an `if`, `set -e` is off for the whole call, so a describe that failed
    # (AccessDenied, say) used to read as "not ready yet" and the loop polled on
    # until the deadline, up to three 25-minute waits on the start path. Here the
    # failed describe ends the run at once, with describe_or_die's own message.
    local description="$1" ready="$2"
    shift 2
    local deadline=$((SECONDS + WAIT_SECONDS)) current
    while true; do
        current="$("$@")" || die "${description}: the state could not be read; stopping here."
        if [[ " ${ready} " == *" ${current} "* ]]; then
            log "   ${description}: ready."
            return 0
        fi
        if [[ "${SECONDS}" -ge "${deadline}" ]]; then
            warn "${description}: still '${current}' after ${WAIT_SECONDS}s."
            return 1
        fi
        sleep "${WAIT_POLL_SECONDS:-15}"
    done
}

# Settled means "a start would be accepted or is unnecessary": stopped, or
# already available/running. `stopping` and `starting` are the transient states
# that a start cannot act on.
RDS_SETTLED_STATES="stopped available"
EC2_SETTLED_STATES="stopped running"

# The start path is split in two because the apply needs it split (FLIP#1596).
#
#   start-data      RDS and the hub host, and the wait for RDS. Terraform
#                   refreshes RDS on every plan, so this much has to happen
#                   BEFORE terraform_apply.yml plans.
#   start-services  the ECS desired counts. This must happen AFTER the plan is
#                   gated, because the gate's whole reason for passing a develop
#                   apply unattended is that the FL services are at 0. Restore
#                   them first and every apply is back to demanding an
#                   fl_quiesced dispatch, which is the thing FLIP#1596 set out to
#                   remove.
#
# `start` is both, in that order, for an operator or a start-stag dispatch where
# there is no plan in between.

FAILURES=0

start_rds_now() {
    aws_rw rds start-db-instance --db-instance-identifier "${RDS_INSTANCE_ID}" >/dev/null ||
        die "start-db-instance failed for ${RDS_INSTANCE_ID}"
    log "   stopped -> starting."
}

start_ec2_now() {
    local id="$1"
    if aws_rw ec2 start-instances --instance-ids "${id}" >/dev/null; then
        log "   ${id}: stopped -> pending."
    else
        # A warning, not a failure: this is the SSM bastion (main.tf), which
        # neither the plan nor the apply uses, and do_status leaves it out of
        # its verdict too. Failing start-data here would skip the apply.
        warn "${id}: start-instances failed; the plan and apply do not need this host."
    fi
}

start_data() {
    local status instance_id state id

    log ""
    log ">> Starting RDS ${RDS_INSTANCE_ID}"
    status="$(rds_status)"
    case "${status}" in
    None)
        die "${RDS_INSTANCE_ID} reports no status; nothing here can start it."
        ;;
    available) log "   already available." ;;
    stopped)
        start_rds_now
        ;;
    *)
        # `stopping` is the normal case, not an edge: a merge made during a hold
        # window arrives seconds after the previous run issued its stop, and RDS
        # takes minutes to settle. Logging "waiting for it to settle" and then
        # never starting anything meant that merge's apply was skipped after the
        # full wait timed out. So wait for the transient state to end, then act
        # on what it ended at.
        log "   currently '${status}', waiting for it to settle before starting."
        if [[ "${DRY_RUN}" == "true" ]]; then
            log "   DRY RUN: not waiting for it to settle."
        elif wait_for "RDS ${RDS_INSTANCE_ID} to settle" "${RDS_SETTLED_STATES}" rds_status; then
            status="$(rds_status)"
            if [[ "${status}" == "stopped" ]]; then
                start_rds_now
            else
                log "   settled at '${status}'."
            fi
        else
            warn "RDS ${RDS_INSTANCE_ID} was still '${status}' after ${WAIT_SECONDS}s; it was never started."
            FAILURES=$((FAILURES + 1))
        fi
        ;;
    esac

    log ""
    log ">> Starting the hub EC2 host (tag Name=${EC2_NAME_TAG})"
    instance_id="$(hub_instance_id)"
    if [[ -z "${instance_id}" ]]; then
        warn "no instance matches tag Name=${EC2_NAME_TAG}; the next apply creates it."
    else
        for id in ${instance_id}; do
            state="$(hub_instance_state "${id}")"
            case "${state}" in
            running | pending) log "   ${id}: already ${state}." ;;
            stopped) start_ec2_now "${id}" ;;
            *)
                # Same reasoning as RDS above: `stopping` is where a merge during
                # a hold finds the host, and leaving it to settle meant leaving it
                # stopped.
                log "   ${id}: '${state}', waiting for it to settle before starting."
                if [[ "${DRY_RUN}" == "true" ]]; then
                    log "   DRY RUN: not waiting for it to settle."
                elif wait_for "EC2 ${id} to settle" "${EC2_SETTLED_STATES}" hub_instance_state "${id}"; then
                    state="$(hub_instance_state "${id}")"
                    if [[ "${state}" == "stopped" ]]; then
                        start_ec2_now "${id}"
                    else
                        log "   ${id}: settled at '${state}'."
                    fi
                else
                    # A warning for the same reason as start_ec2_now's.
                    warn "${id}: still '${state}' after ${WAIT_SECONDS}s; it was never started. The plan and apply do not need this host."
                fi
                ;;
            esac
        done
    fi

    if [[ "${DRY_RUN}" == "true" ]]; then
        log ""
        log "   DRY RUN: skipping the wait for RDS."
        return 0
    fi

    log ""
    log ">> Waiting for RDS to become available (up to ${WAIT_SECONDS}s)"
    wait_for "RDS ${RDS_INSTANCE_ID}" "available" rds_status || FAILURES=$((FAILURES + 1))
}

# ecs_services.tf ignores desired_count (FLIP#1596), so an apply will not put the
# services back by itself. Setting them here is what makes "the next develop
# apply brings staging back with no manual steps" true.
start_services() {
    local service count

    log ""
    log ">> Restoring the ECS desired counts to ${RUNNING_DESIRED_COUNT}"
    for service in ${ECS_SERVICES}; do
        count="$(service_desired_count "${service}")"
        if [[ -z "${count}" || "${count}" == "None" ]]; then
            log "   ${service}: not present yet, the apply creates it."
            continue
        fi
        if [[ "${count}" == "${RUNNING_DESIRED_COUNT}" ]]; then
            log "   ${service}: already at ${count}."
            continue
        fi
        if aws_rw ecs update-service --cluster "${ECS_CLUSTER}" --service "${service}" \
            --desired-count "${RUNNING_DESIRED_COUNT}" >/dev/null; then
            log "   ${service}: ${count} -> ${RUNNING_DESIRED_COUNT}."
        else
            warn "${service}: update-service failed."
            FAILURES=$((FAILURES + 1))
        fi
    done
}

finish_start() {
    local what="$1"
    log ""
    [[ "${FAILURES}" -eq 0 ]] || die "${FAILURES} start action(s) failed; staging is only partly up."
    log "✅ ${what}"
    if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
        {
            echo "### ☀️ Staging started"
            echo
            echo "${what}"
        } >>"${GITHUB_STEP_SUMMARY}"
    fi
}

do_start() {
    assert_staging_target
    require_aws
    start_data
    start_services
    finish_start "Staging started. RDS available, EC2 running, ECS at ${RUNNING_DESIRED_COUNT}."
}

do_start_data() {
    assert_staging_target
    require_aws
    start_data
    finish_start "RDS is available and the hub host is running. The ECS services are left where they are, so the FL gate can still see a stopped estate."
}

do_start_services() {
    assert_staging_target
    require_aws
    start_services
    finish_start "ECS desired counts restored to ${RUNNING_DESIRED_COUNT}."
}

# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

# Read only, so it does NOT take the production guard: the drift job and the FL
# gate both ask this question about whichever estate they are already pointed at,
# production included, and refusing there would make them fail rather than report.
#
# Emits shell-ish key=value lines on stdout, and the same keys to $GITHUB_OUTPUT
# when a workflow is reading them as step outputs.
do_status() {
    require_aws
    local service count fl_stopped=true all_zero=true rds ec2_state instance_id ec2_stopped=true

    for service in ${ECS_SERVICES}; do
        count="$(service_desired_count "${service}")"
        [[ -n "${count}" && "${count}" != "None" ]] || continue
        [[ "${count}" == "0" ]] || all_zero=false
    done
    for service in ${FL_ECS_SERVICES}; do
        count="$(service_desired_count "${service}")"
        [[ -n "${count}" && "${count}" != "None" ]] || continue
        [[ "${count}" == "0" ]] || fl_stopped=false
    done

    rds="$(rds_status)"
    [[ -n "${rds}" ]] || rds="unknown"

    instance_id="$(hub_instance_id)"
    for id in ${instance_id}; do
        ec2_state="$(hub_instance_state "${id}")"
        [[ "${ec2_state}" == "stopped" || "${ec2_state}" == "stopping" ]] || ec2_stopped=false
    done

    # "stopped" means the estate is in the state the stop job leaves it in: every
    # service at 0 and RDS not available. EC2 is reported but deliberately not
    # part of the verdict. A bastion left running is a cost question, not a
    # reason to call the estate up and then plan against a database that is not.
    local stopped=false
    if [[ "${all_zero}" == "true" && "${rds}" != "available" ]]; then
        stopped=true
    fi

    printf 'stopped=%s\n' "${stopped}"
    printf 'fl_stopped=%s\n' "${fl_stopped}"
    printf 'ecs_all_zero=%s\n' "${all_zero}"
    printf 'ec2_stopped=%s\n' "${ec2_stopped}"
    printf 'rds_status=%s\n' "${rds}"

    if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
        {
            printf 'stopped=%s\n' "${stopped}"
            printf 'fl_stopped=%s\n' "${fl_stopped}"
            printf 'ecs_all_zero=%s\n' "${all_zero}"
            printf 'ec2_stopped=%s\n' "${ec2_stopped}"
            printf 'rds_status=%s\n' "${rds}"
        } >>"${GITHUB_OUTPUT}"
    fi
}

# ---------------------------------------------------------------------------
# hold-minutes
# ---------------------------------------------------------------------------

# Resolve the hold window from, in order: the dispatch input, the repository
# variable, the built-in default. Anything that is not a plain non-negative
# integer is rejected rather than coerced, because the two plausible coercions
# both have a bad failure mode: 0 stops staging the moment the deploy lands, and
# "treat as the default" hides a typo in a repository variable for months.
do_hold_minutes() {
    local candidate source
    if [[ -n "${HOLD_MINUTES_INPUT:-}" ]]; then
        candidate="${HOLD_MINUTES_INPUT}"
        source="the dispatch input"
    elif [[ -n "${HOLD_MINUTES_VAR:-}" ]]; then
        candidate="${HOLD_MINUTES_VAR}"
        source="the repository variable STAGING_HOLD_MINUTES"
    else
        candidate="${DEFAULT_HOLD_MINUTES}"
        source="the built-in default"
    fi

    # At most four digits, read as base 10: bash treats a leading zero as octal,
    # so `010` became 8 minutes and `08` failed, and a long enough value wraps.
    [[ "${candidate}" =~ ^[0-9]{1,4}$ ]] ||
        die "hold minutes must be a whole number from 0 to 9999, got '${candidate}' from ${source}"

    local minutes=$((10#${candidate}))
    if [[ "${minutes}" -gt "${MAX_HOLD_MINUTES}" ]]; then
        warn "hold of ${minutes} minutes from ${source} exceeds the ${MAX_HOLD_MINUTES} minute ceiling; clamping. A GitHub job is killed at six hours, and a hold that is killed never stops anything."
        minutes="${MAX_HOLD_MINUTES}"
    fi

    printf 'minutes=%s\n' "${minutes}"
    printf 'seconds=%s\n' "$((minutes * 60))"
    if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
        {
            printf 'minutes=%s\n' "${minutes}"
            printf 'seconds=%s\n' "$((minutes * 60))"
        } >>"${GITHUB_OUTPUT}"
    fi
    log "Hold window: ${minutes} minute(s), from ${source}." >&2
}

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

ACTION="${1:-}"
[[ -n "${ACTION}" ]] || usage_die "usage: ${SELF} <stop|start|start-data|start-services|status|hold-minutes>"
shift
[[ $# -eq 0 ]] || usage_die "${SELF} takes no arguments after the action (got: $*)"

case "${ACTION}" in
stop) do_stop ;;
start) do_start ;;
start-data) do_start_data ;;
start-services) do_start_services ;;
status) do_status ;;
hold-minutes) do_hold_minutes ;;
*) usage_die "unknown action '${ACTION}'; expected stop, start, start-data, start-services, status or hold-minutes" ;;
esac
