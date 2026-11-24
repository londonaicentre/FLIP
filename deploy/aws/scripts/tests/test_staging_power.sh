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

# Black-box tests for scripts/staging-power.sh (FLIP#1596).
#
# Drives the REAL script with a stub `aws` on PATH. The stub records every call to
# a log file and answers describe calls from fixture variables, so the tests can
# assert on exactly which mutating calls were made and in what shape, without any
# credentials and without reaching AWS.
#
# The assertions that matter most are the refusals. staging-power.sh stops ECS,
# EC2 and RDS by names that are identical in both estates, so "it declines to run
# against production" is the property the script exists to keep, and it is pinned
# here from every direction: the production environment name, the production
# TF_HUB value, the main branch, and each of those values being absent.
#
# Usage:
#     bash deploy/providers/AWS/scripts/tests/test_staging_power.sh

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$(cd "${HERE}/.." && pwd)/staging-power.sh"

# A GitHub runner exports these into every step. Inheriting them would make the
# harness append its fixture summaries to the real job summary, and would make the
# "no annotation outside Actions" style assertions untestable.
unset GITHUB_ACTIONS GITHUB_STEP_SUMMARY GITHUB_OUTPUT
unset FLIP_ENVIRONMENT TF_HUB GITHUB_REF_NAME
unset HOLD_MINUTES_INPUT HOLD_MINUTES_VAR

TEST_ROOT="$(mktemp -d)"
trap 'rm -rf "${TEST_ROOT}"' EXIT

PASS=0
FAIL=0

ok() {
    echo "   ✅ $1"
    PASS=$((PASS + 1))
}

no() {
    echo "   ❌ $1"
    shift
    for line in "$@"; do echo "      ${line}"; done
    FAIL=$((FAIL + 1))
}

section() {
    echo ""
    echo "-- $1"
}

# ---------------------------------------------------------------------------
# The aws stub
# ---------------------------------------------------------------------------
#
# Reads its answers from the environment so each test can set a world:
#   STUB_ECS_<service with - as _>   desiredCount, or "ABSENT"
#   STUB_EC2_ID                     instance id, or empty for "no instance"
#   STUB_EC2_STATE                  running | stopped | ...
#   STUB_RDS_STATUS                 available | stopped | ...
#   STUB_FAIL_ON                    a substring; any call containing it exits 1
# Every invocation is appended to ${AWS_CALL_LOG}.

STUB_DIR="${TEST_ROOT}/bin"
mkdir -p "${STUB_DIR}"
cat >"${STUB_DIR}/aws" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${AWS_CALL_LOG}"

if [[ -n "${STUB_FAIL_ON:-}" ]] && [[ "$*" == *"${STUB_FAIL_ON}"* ]]; then
    echo "stubbed failure" >&2
    exit 1
fi

# The script always invokes `aws --region <region> <service> <verb> ...`, so the
# service and verb are at fixed positions. Anything else is a call this stub was
# not written for, and falls through to the catch-all below.
args=("$@")
service="${args[2]:-}"
verb="${args[3]:-}"

value_of() {
    local flag="$1" n=${#args[@]} i
    for ((i = 0; i < n - 1; i++)); do
        [[ "${args[$i]}" == "${flag}" ]] && { printf '%s' "${args[$((i + 1))]}"; return 0; }
    done
    return 1
}

case "${service}:${verb}" in
ecs:describe-services)
    svc="$(value_of --services)"
    key="STUB_ECS_${svc//-/_}"
    answer="${!key:-ABSENT}"
    [[ "${answer}" == "ABSENT" ]] && exit 0
    printf '%s\n' "${answer}"
    ;;
ecs:update-service) exit 0 ;;
ec2:describe-instances)
    if [[ -z "${STUB_EC2_ID:-}" ]]; then exit 0; fi
    if [[ "$*" == *"--instance-ids"* ]]; then
        # A transient state settles after a fixed number of describes, the way a
        # real `stopping` instance reaches `stopped` a few minutes later. Without
        # this the start path could only ever be tested against a frozen world.
        if [[ -n "${STUB_EC2_SETTLE_AFTER:-}" ]]; then
            seen=0
            [[ -f "${AWS_CALL_LOG}.ec2-describes" ]] && seen="$(cat "${AWS_CALL_LOG}.ec2-describes")"
            seen=$((seen + 1))
            printf '%s' "${seen}" >"${AWS_CALL_LOG}.ec2-describes"
            if [[ "${seen}" -gt "${STUB_EC2_SETTLE_AFTER}" ]]; then
                printf '%s\n' "${STUB_EC2_SETTLED_STATE:-stopped}"
                exit 0
            fi
        fi
        printf '%s\n' "${STUB_EC2_STATE:-running}"
    else
        printf '%s\n' "${STUB_EC2_ID}"
    fi
    ;;
ec2:start-instances | ec2:stop-instances) exit 0 ;;
rds:describe-db-instances)
    # A started instance answers "available" from then on, so the start path's
    # wait loop terminates the way it would against a real RDS instead of
    # spinning until the deadline.
    [[ -f "${AWS_CALL_LOG}.rds-started" ]] && { printf 'available\n'; exit 0; }
    [[ -n "${STUB_RDS_STATUS:-}" ]] || exit 1
    # Answer normally for STUB_RDS_DENY_AFTER describes, then refuse, the way an
    # expired session or a narrowed role does part way through a wait.
    if [[ -n "${STUB_RDS_DENY_AFTER:-}" ]]; then
        denied=0
        [[ -f "${AWS_CALL_LOG}.rds-deny" ]] && denied="$(cat "${AWS_CALL_LOG}.rds-deny")"
        denied=$((denied + 1))
        printf '%s' "${denied}" >"${AWS_CALL_LOG}.rds-deny"
        if [[ "${denied}" -gt "${STUB_RDS_DENY_AFTER}" ]]; then
            echo "An error occurred (AccessDenied)" >&2
            exit 254
        fi
    fi
    # Same settling behaviour as EC2 above: `stopping` becomes `stopped`.
    if [[ -n "${STUB_RDS_SETTLE_AFTER:-}" ]]; then
        seen=0
        [[ -f "${AWS_CALL_LOG}.rds-describes" ]] && seen="$(cat "${AWS_CALL_LOG}.rds-describes")"
        seen=$((seen + 1))
        printf '%s' "${seen}" >"${AWS_CALL_LOG}.rds-describes"
        if [[ "${seen}" -gt "${STUB_RDS_SETTLE_AFTER}" ]]; then
            printf '%s\n' "${STUB_RDS_SETTLED_STATUS:-stopped}"
            exit 0
        fi
    fi
    printf '%s\n' "${STUB_RDS_STATUS}"
    ;;
rds:start-db-instance)
    : >"${AWS_CALL_LOG}.rds-started"
    ;;
rds:stop-db-instance) exit 0 ;;
*)
    echo "aws stub: unexpected call: $*" >&2
    exit 64
    ;;
esac
STUB
chmod +x "${STUB_DIR}/aws"
PATH="${STUB_DIR}:${PATH}"
export PATH

# Default world: staging fully up.
up_world() {
    export STUB_ECS_flip_api=1
    export STUB_ECS_fl_server_net_1=1
    export STUB_ECS_fl_api_net_1=1
    export STUB_EC2_ID="i-0abcdef1234567890"
    export STUB_EC2_STATE="running"
    export STUB_RDS_STATUS="available"
    unset STUB_FAIL_ON
    unset STUB_RDS_SETTLE_AFTER STUB_RDS_SETTLED_STATUS STUB_RDS_DENY_AFTER
    unset STUB_EC2_SETTLE_AFTER STUB_EC2_SETTLED_STATE
}

down_world() {
    export STUB_ECS_flip_api=0
    export STUB_ECS_fl_server_net_1=0
    export STUB_ECS_fl_api_net_1=0
    export STUB_EC2_ID="i-0abcdef1234567890"
    export STUB_EC2_STATE="stopped"
    export STUB_RDS_STATUS="stopped"
    unset STUB_FAIL_ON
    unset STUB_RDS_SETTLE_AFTER STUB_RDS_SETTLED_STATUS STUB_RDS_DENY_AFTER
    unset STUB_EC2_SETTLE_AFTER STUB_EC2_SETTLED_STATE
}

stag_env() {
    export FLIP_ENVIRONMENT="aws-stag"
    export TF_HUB="stag"
    export GITHUB_REF_NAME="develop"
}

# run <action> -> sets RC, STDOUT, STDERR, and AWS_CALL_LOG holds the calls.
run() {
    AWS_CALL_LOG="${TEST_ROOT}/calls-$$-${RANDOM}.log"
    export AWS_CALL_LOG
    : >"${AWS_CALL_LOG}"
    STDOUT="$(bash "${SCRIPT}" "$@" 2>"${TEST_ROOT}/err")"
    RC=$?
    STDERR="$(cat "${TEST_ROOT}/err")"
}

expect_rc() {
    local want="$1" title="$2"
    [[ "${RC}" -eq "${want}" ]] && ok "${title}" ||
        no "${title}" "expected rc ${want}, got ${RC}" "stdout: ${STDOUT}" "stderr: ${STDERR}"
}

called() { grep -qF -- "$1" "${AWS_CALL_LOG}"; }

expect_called() {
    local needle="$1" title="$2"
    called "${needle}" && ok "${title}" || no "${title}" "no call matching: ${needle}" "$(cat "${AWS_CALL_LOG}")"
}

expect_not_called() {
    local needle="$1" title="$2"
    called "${needle}" && no "${title}" "unexpected call matching: ${needle}" "$(cat "${AWS_CALL_LOG}")" ||
        ok "${title}"
}

expect_stdout() {
    local needle="$1" title="$2"
    printf '%s' "${STDOUT}" | grep -qF -- "${needle}" && ok "${title}" ||
        no "${title}" "stdout did not contain: ${needle}" "${STDOUT}"
}

# A whole stdout line, for key=value output where one key is a suffix of
# another: a substring match for `stopped=false` is also satisfied by
# `fl_stopped=false`.
expect_stdout_line() {
    local line="$1" title="$2"
    printf '%s\n' "${STDOUT}" | grep -qxF -- "${line}" && ok "${title}" ||
        no "${title}" "stdout had no line: ${line}" "${STDOUT}"
}

expect_stderr() {
    local needle="$1" title="$2"
    printf '%s' "${STDERR}" | grep -qF -- "${needle}" && ok "${title}" ||
        no "${title}" "stderr did not contain: ${needle}" "${STDERR}"
}

echo "==== staging-power.sh ===="

# ---------------------------------------------------------------------------
section "usage"
# ---------------------------------------------------------------------------
up_world
stag_env
run
expect_rc 2 "no action is a usage error"
run bogus
expect_rc 2 "an unknown action is a usage error"
run stop extra
expect_rc 2 "a trailing argument is a usage error"

# ---------------------------------------------------------------------------
section "the production guard refuses, from every direction"
# ---------------------------------------------------------------------------
up_world

for action in stop start; do
    stag_env
    export FLIP_ENVIRONMENT="aws-prod"
    run "${action}"
    expect_rc 1 "${action} refuses FLIP_ENVIRONMENT=aws-prod"
    expect_not_called "update-service" "${action} on aws-prod makes no mutating ECS call"
    expect_not_called "stop-instances" "${action} on aws-prod makes no mutating EC2 call"
    expect_not_called "stop-db-instance" "${action} on aws-prod makes no mutating RDS call"
    expect_stderr "Refusing to run" "${action} on aws-prod says why"

    stag_env
    export TF_HUB="prod"
    run "${action}"
    expect_rc 1 "${action} refuses TF_HUB=prod"
    expect_not_called "update-service" "${action} with TF_HUB=prod makes no mutating call"

    stag_env
    export TF_HUB="lza-stag"
    run "${action}"
    expect_rc 1 "${action} refuses the pre-#1479 token TF_HUB=lza-stag"

    stag_env
    export GITHUB_REF_NAME="main"
    run "${action}"
    expect_rc 1 "${action} refuses a run on main"
    expect_not_called "update-service" "${action} on main makes no mutating call"

    stag_env
    unset FLIP_ENVIRONMENT
    run "${action}"
    expect_rc 1 "${action} refuses when FLIP_ENVIRONMENT is unset"

    stag_env
    unset TF_HUB
    run "${action}"
    expect_rc 1 "${action} refuses when TF_HUB is unset"

    stag_env
    export FLIP_ENVIRONMENT=""
    run "${action}"
    expect_rc 1 "${action} refuses an empty FLIP_ENVIRONMENT"

    stag_env
    export FLIP_ENVIRONMENT="aws-stag-prod"
    run "${action}"
    expect_rc 1 "${action} refuses a lookalike environment name"
done

# A refusal must be visible on the run summary, not only in the log.
stag_env
export FLIP_ENVIRONMENT="aws-prod"
summary="${TEST_ROOT}/summary-refusal.md"
: >"${summary}"
AWS_CALL_LOG="${TEST_ROOT}/calls-refusal.log"
export AWS_CALL_LOG
: >"${AWS_CALL_LOG}"
GITHUB_STEP_SUMMARY="${summary}" bash "${SCRIPT}" stop >/dev/null 2>&1
rc=$?
[[ "${rc}" -eq 1 ]] && ok "a refusal under Actions still exits 1" || no "a refusal under Actions still exits 1" "got ${rc}"
grep -q "refused" "${summary}" && ok "the refusal is written to the job summary" ||
    no "the refusal is written to the job summary" "$(cat "${summary}")"

# ---------------------------------------------------------------------------
section "stop on a running staging estate"
# ---------------------------------------------------------------------------
up_world
stag_env
run stop
expect_rc 0 "stop succeeds"
expect_called "ecs update-service --cluster flip-cluster --service flip-api --desired-count 0" \
    "flip-api is scaled to 0"
expect_called "ecs update-service --cluster flip-cluster --service fl-server-net-1 --desired-count 0" \
    "fl-server-net-1 is scaled to 0"
expect_called "ecs update-service --cluster flip-cluster --service fl-api-net-1 --desired-count 0" \
    "fl-api-net-1 is scaled to 0"
expect_called "ec2 stop-instances --instance-ids i-0abcdef1234567890" "the hub host is stopped"
expect_called "rds stop-db-instance --db-instance-identifier flip-database" "RDS is stopped"
expect_stdout "Staging stop requested" "stop reports success"

# ---------------------------------------------------------------------------
section "stop is idempotent"
# ---------------------------------------------------------------------------
down_world
stag_env
run stop
expect_rc 0 "stop on an already stopped estate succeeds"
expect_not_called "update-service" "nothing is scaled when every service is already at 0"
expect_not_called "stop-instances" "the host is not stopped twice"
expect_not_called "stop-db-instance" "RDS is not stopped twice"

# ---------------------------------------------------------------------------
section "stop tolerates an absent service and an unstoppable RDS"
# ---------------------------------------------------------------------------
up_world
stag_env
export STUB_ECS_fl_api_net_1="ABSENT"
run stop
expect_rc 0 "an absent service is not a failure"
expect_called "--service flip-api --desired-count 0" "the services that do exist are still scaled"

up_world
stag_env
export STUB_RDS_STATUS="backing-up"
run stop
expect_rc 0 "an RDS mid-backup is left alone rather than failing the job"
expect_not_called "stop-db-instance" "no stop is attempted on a backing-up instance"
expect_stderr "nightly guard will retry" "the operator is told the guard will retry"

up_world
stag_env
export STUB_RDS_STATUS="None"
run stop
expect_rc 0 "an absent RDS is not a failure"
expect_not_called "stop-db-instance" "no stop is attempted on an absent RDS"

# A failed mutating call must fail the job: a stop that half worked and reported
# green is how an estate stays up while everyone believes it is down.
up_world
stag_env
export STUB_FAIL_ON="update-service"
run stop
expect_rc 1 "a failed update-service fails the stop"
unset STUB_FAIL_ON

# ---------------------------------------------------------------------------
section "DRY_RUN makes no mutating call"
# ---------------------------------------------------------------------------
up_world
stag_env
export DRY_RUN=true
run stop
expect_rc 0 "a dry run succeeds"
expect_not_called "update-service" "a dry run does not scale anything"
expect_not_called "stop-instances" "a dry run does not stop the host"
expect_not_called "stop-db-instance" "a dry run does not stop RDS"
expect_stderr "DRY RUN" "a dry run says what it would have done"
unset DRY_RUN

# ---------------------------------------------------------------------------
section "start brings the estate back"
# ---------------------------------------------------------------------------
down_world
stag_env
export WAIT_POLL_SECONDS=1
run start
expect_rc 0 "start succeeds"
expect_called "rds start-db-instance --db-instance-identifier flip-database" "RDS is started"
expect_called "ec2 start-instances --instance-ids i-0abcdef1234567890" "the hub host is started"
expect_stdout "ready" "start waits for RDS to become available before returning"
expect_called "--service flip-api --desired-count 1" "the desired counts are restored after the wait"

# With RDS already available there is no wait to skip, so this runs for real and
# proves the desired counts are restored, the part that makes a develop merge
# bring staging back with no manual step, now that Terraform ignores the count.
down_world
stag_env
export STUB_RDS_STATUS="available"
run start
expect_rc 0 "start succeeds when RDS is already available"
expect_not_called "start-db-instance" "an available RDS is not started again"
expect_called "ecs update-service --cluster flip-cluster --service flip-api --desired-count 1" \
    "flip-api is restored to 1"
expect_called "ecs update-service --cluster flip-cluster --service fl-server-net-1 --desired-count 1" \
    "fl-server-net-1 is restored to 1"
expect_called "ecs update-service --cluster flip-cluster --service fl-api-net-1 --desired-count 1" \
    "fl-api-net-1 is restored to 1"

# The split start: start-data must NOT touch the ECS counts, because the FL gate
# in terraform_apply.yml reads them between the two halves and only passes a
# develop apply unattended while they are at 0.
down_world
stag_env
run start-data
expect_rc 0 "start-data succeeds"
expect_called "rds start-db-instance" "start-data starts RDS"
expect_called "ec2 start-instances" "start-data starts the hub host"
expect_not_called "update-service" "start-data leaves the ECS counts alone"

# A torn-down estate has no RDS yet. The apply after start-data creates it, so a
# missing instance must not fail start-data and skip that apply.
down_world
stag_env
export STUB_RDS_STATUS="None" WAIT_SECONDS=2
run start-data
expect_rc 0 "start-data succeeds when RDS does not exist yet"
expect_not_called "start-db-instance" "an absent RDS is not started"
unset WAIT_SECONDS

down_world
stag_env
run start-services
expect_rc 0 "start-services succeeds"
expect_called "ecs update-service --cluster flip-cluster --service fl-server-net-1 --desired-count 1" \
    "start-services restores the FL server"
expect_not_called "start-db-instance" "start-services does not touch RDS"
expect_not_called "start-instances" "start-services does not touch the host"

for split in start-data start-services; do
    stag_env
    export FLIP_ENVIRONMENT="aws-prod"
    run "${split}"
    expect_rc 1 "${split} refuses production too"
    expect_not_called "update-service" "${split} on aws-prod makes no mutating call"
done

# RDS restarts itself seven days after a stop, which is exactly the state the
# nightly guard has to notice. Start must cope with finding it already up.
up_world
stag_env
run start
expect_rc 0 "start on an already running estate succeeds"
expect_not_called "start-db-instance" "nothing is started twice"
expect_not_called "update-service" "the desired counts are left alone when already correct"

# ---------------------------------------------------------------------------
section "status reports the estate without the production guard"
# ---------------------------------------------------------------------------
down_world
unset FLIP_ENVIRONMENT TF_HUB
export GITHUB_REF_NAME="main"
run status
expect_rc 0 "status runs with no environment set and on main"
expect_stdout_line "stopped=true" "a stopped estate reports stopped=true"
expect_stdout "fl_stopped=true" "a stopped estate reports fl_stopped=true"
expect_stdout "rds_status=stopped" "the RDS status is reported"
expect_not_called "update-service" "status makes no mutating call"
expect_not_called "stop-instances" "status makes no mutating EC2 call"
expect_not_called "stop-db-instance" "status makes no mutating RDS call"

up_world
run status
expect_rc 0 "status on a running estate succeeds"
expect_stdout_line "stopped=false" "a running estate reports stopped=false"
expect_stdout "fl_stopped=false" "a running estate reports fl_stopped=false"

# The FL gate's question is narrower than the estate's: FL at 0 with flip-api up
# is still "no training run can be in flight", which is what lets a docs-only
# develop apply through without an fl_quiesced dispatch.
up_world
export STUB_ECS_fl_server_net_1=0
export STUB_ECS_fl_api_net_1=0
run status
expect_stdout "fl_stopped=true" "FL at 0 with flip-api up still reports fl_stopped=true"
expect_stdout_line "stopped=false" "but the estate is not called stopped while flip-api runs"

# RDS available with every service at 0 is a half started estate, and planning
# against it is fine, so it must not be reported as stopped.
down_world
export STUB_RDS_STATUS="available"
run status
expect_stdout_line "stopped=false" "ECS at 0 but RDS available is not 'stopped'"

# status writes its keys to GITHUB_OUTPUT so a workflow can branch on them.
down_world
outfile="${TEST_ROOT}/gh-output.txt"
: >"${outfile}"
AWS_CALL_LOG="${TEST_ROOT}/calls-output.log"
export AWS_CALL_LOG
: >"${AWS_CALL_LOG}"
GITHUB_OUTPUT="${outfile}" bash "${SCRIPT}" status >/dev/null 2>&1
grep -q "^stopped=true$" "${outfile}" && ok "status writes stopped= to GITHUB_OUTPUT" ||
    no "status writes stopped= to GITHUB_OUTPUT" "$(cat "${outfile}")"
grep -q "^fl_stopped=true$" "${outfile}" && ok "status writes fl_stopped= to GITHUB_OUTPUT" ||
    no "status writes fl_stopped= to GITHUB_OUTPUT" "$(cat "${outfile}")"

# ---------------------------------------------------------------------------
section "hold-minutes resolution"
# ---------------------------------------------------------------------------
unset HOLD_MINUTES_INPUT HOLD_MINUTES_VAR
run hold-minutes
expect_rc 0 "the default resolves"
expect_stdout "minutes=120" "the default hold window is 120 minutes"
expect_stdout "seconds=7200" "the default is also reported in seconds"

HOLD_MINUTES_VAR=45 run hold-minutes
expect_stdout "minutes=45" "a repository variable overrides the default"

HOLD_MINUTES_VAR=45 HOLD_MINUTES_INPUT=15 run hold-minutes
expect_stdout "minutes=15" "a dispatch input overrides the repository variable"

HOLD_MINUTES_INPUT=0 run hold-minutes
expect_rc 0 "zero is a legitimate hold: stop immediately after the check"
expect_stdout "minutes=0" "zero resolves to zero"

HOLD_MINUTES_INPUT=9999 run hold-minutes
expect_rc 0 "an over-long hold is clamped rather than rejected"
expect_stdout "minutes=300" "the hold is clamped to the ceiling"
expect_stderr "clamping" "the clamp is explained"

# A leading zero is decimal, not octal: bash read 010 as 8 and 08 as an error.
HOLD_MINUTES_INPUT=010 run hold-minutes
expect_rc 0 "a zero-padded hold is accepted"
expect_stdout_line "minutes=10" "010 is ten minutes, not octal eight"
expect_stdout_line "seconds=600" "and 600 seconds"
HOLD_MINUTES_INPUT=08 run hold-minutes
expect_rc 0 "08 is not an invalid octal number"
expect_stdout_line "minutes=8" "08 is eight minutes"
HOLD_MINUTES_INPUT=12345678901234567890 run hold-minutes
expect_rc 1 "a value long enough to wrap is rejected"

HOLD_MINUTES_INPUT="two hours" run hold-minutes
expect_rc 1 "a non-numeric hold is rejected"
HOLD_MINUTES_INPUT="-5" run hold-minutes
expect_rc 1 "a negative hold is rejected"
HOLD_MINUTES_VAR="120m" run hold-minutes
expect_rc 1 "a typo in the repository variable is rejected, not silently defaulted"

# ---------------------------------------------------------------------------
section "a start that arrives while the previous stop is still settling"
# ---------------------------------------------------------------------------
# The normal case for a merge made during a hold window: the stop was issued
# seconds ago, so RDS reads `stopping` and the hub host reads `stopping`. The
# start must wait for each to settle and then start it, not log "leaving it to
# settle" and return with the estate still down.
up_world
stag_env
export WAIT_POLL_SECONDS=0
export STUB_RDS_STATUS="stopping"
export STUB_RDS_SETTLE_AFTER=2
export STUB_RDS_SETTLED_STATUS="stopped"
export STUB_EC2_STATE="stopping"
export STUB_EC2_SETTLE_AFTER=2
export STUB_EC2_SETTLED_STATE="stopped"
run start-data
expect_rc 0 "start-data succeeds against an estate that is still stopping"
expect_called "rds start-db-instance --db-instance-identifier flip-database" \
    "a stopping RDS is started once it settles at stopped"
expect_called "ec2 start-instances --instance-ids i-0abcdef1234567890" \
    "a stopping hub host is started once it settles at stopped"
expect_stdout "waiting for it to settle before starting" "the wait is explained"

# Settling back to available or running is not a failure and needs no start.
up_world
stag_env
export WAIT_POLL_SECONDS=0
export STUB_RDS_STATUS="starting"
export STUB_RDS_SETTLE_AFTER=1
export STUB_RDS_SETTLED_STATUS="available"
export STUB_EC2_STATE="pending"
run start-data
expect_rc 0 "an estate settling back up is not a failure"
expect_not_called "start-db-instance" "an RDS that settles at available is not started again"

# A transient state that never settles must fail the start rather than report a
# running estate: the apply that follows would plan against a stopped database.
up_world
stag_env
export WAIT_SECONDS=0
export WAIT_POLL_SECONDS=0
export STUB_RDS_STATUS="stopping"
export STUB_EC2_STATE="running"
run start-data
expect_rc 1 "a transient RDS that never settles fails the start"
expect_stderr "never started" "the failure says the instance was never started"
up_world
stag_env
unset WAIT_SECONDS
export WAIT_POLL_SECONDS=0

# A describe that fails part way through a wait ends the run at once. Inside
# `if probe`, `set -e` is off, so it used to read as "not ready" and poll on to
# the deadline.
up_world
stag_env
export WAIT_SECONDS=30
export WAIT_POLL_SECONDS=0
export STUB_RDS_STATUS="stopping"
export STUB_RDS_DENY_AFTER=2
started=${SECONDS}
run start-data
expect_rc 1 "a describe denied during the settle wait fails the start"
expect_stderr "could not describe" "the failure names the describe"
expect_not_called "start-db-instance" "nothing is started on a state that was never read"
[[ $((SECONDS - started)) -lt 10 ]] && ok "it fails at once rather than polling to the deadline" ||
    no "it fails at once rather than polling to the deadline" "took $((SECONDS - started))s"
up_world
stag_env
unset WAIT_SECONDS
export WAIT_POLL_SECONDS=0

# The hub host is the SSM bastion; the plan and apply do not use it, so a
# failure to start it is a warning and the apply still runs.
up_world
stag_env
export WAIT_SECONDS=0
export WAIT_POLL_SECONDS=0
export STUB_EC2_STATE="stopping"
run start-data
expect_rc 0 "an EC2 host stuck stopping does not fail start-data"
expect_stderr "never started" "but it is warned about"
down_world
stag_env
export STUB_RDS_STATUS="available"
export STUB_FAIL_ON="start-instances"
run start-data
expect_rc 0 "a failed EC2 start does not fail start-data"
expect_stderr "start-instances failed" "but it is warned about"
up_world
stag_env
unset WAIT_SECONDS
export WAIT_POLL_SECONDS=0

# ---------------------------------------------------------------------------
section "a describe that fails is fatal and says so"
# ---------------------------------------------------------------------------
# `2>/dev/null` on the describe helpers turned an AccessDenied into an exit with
# no message, part way through a stop. Every describe path must now name the
# resource and the CLI's own error.
for action in stop start-data status; do
    up_world
    stag_env
    export STUB_FAIL_ON="describe-db-instances"
    run "${action}"
    expect_rc 1 "${action} fails when the RDS describe is denied"
    expect_stderr "could not describe" "${action} says which describe failed"
    expect_stderr "stubbed failure" "${action} keeps the AWS CLI's own error"
done
up_world
stag_env

export STUB_FAIL_ON="describe-services"
run stop
expect_rc 1 "stop fails when the ECS describe is denied"
expect_not_called "update-service" "nothing is scaled on an estate that could not be read"
unset STUB_FAIL_ON

# The FL gate in terraform_apply.yml reads `status` and releases on
# fl_stopped=true. A denied describe must therefore never produce that line:
# reporting the FL services as stopped while they are up would pass an apply that
# interrupts a training round.
up_world
stag_env
export STUB_FAIL_ON="describe-services"
run status
expect_rc 1 "status fails closed when the ECS describe is denied"
printf '%s' "${STDOUT}" | grep -q "fl_stopped=true" &&
    no "a denied describe never reports fl_stopped=true" "stdout: ${STDOUT}" ||
    ok "a denied describe never reports fl_stopped=true"
printf '%s' "${STDOUT}" | grep -q "stopped=true" &&
    no "a denied describe never reports the estate as stopped" "stdout: ${STDOUT}" ||
    ok "a denied describe never reports the estate as stopped"
unset STUB_FAIL_ON

# ---------------------------------------------------------------------------
section "the stop summary reports what actually happened"
# ---------------------------------------------------------------------------
# It used to hard-code one row per resource reading "stopped", so a stop that
# left RDS up still rendered a fully stopped estate.
summary_run() {
    AWS_CALL_LOG="${TEST_ROOT}/calls-summary-${RANDOM}.log"
    export AWS_CALL_LOG
    : >"${AWS_CALL_LOG}"
    SUMMARY_FILE="${TEST_ROOT}/summary-${RANDOM}.md"
    : >"${SUMMARY_FILE}"
    STDOUT="$(GITHUB_ACTIONS=true GITHUB_STEP_SUMMARY="${SUMMARY_FILE}" \
        bash "${SCRIPT}" "$@" 2>"${TEST_ROOT}/err")"
    RC=$?
    STDERR="$(cat "${TEST_ROOT}/err")"
    SUMMARY="$(cat "${SUMMARY_FILE}")"
}

expect_summary() {
    local needle="$1" title="$2"
    printf '%s' "${SUMMARY}" | grep -qF -- "${needle}" && ok "${title}" ||
        no "${title}" "summary did not contain: ${needle}" "${SUMMARY}"
}

expect_not_summary() {
    local needle="$1" title="$2"
    printf '%s' "${SUMMARY}" | grep -qF -- "${needle}" &&
        no "${title}" "summary unexpectedly contained: ${needle}" "${SUMMARY}" || ok "${title}"
}

up_world
stag_env
export STUB_RDS_STATUS="backing-up"
summary_run stop
expect_rc 0 "a backing-up RDS still exits 0"
expect_summary "Staging partly stopped" "the summary headline says the estate is only partly stopped"
expect_summary "left up" "the RDS row says it was left up"
expect_not_summary "🌙 Staging stopped" "the summary does not claim a full stop"
printf '%s' "${STDOUT}" | grep -q "::warning title=Staging RDS left up::" &&
    ok "an Actions warning annotation is emitted when RDS is left up" ||
    no "an Actions warning annotation is emitted when RDS is left up" "stdout: ${STDOUT}"

up_world
stag_env
summary_run stop
expect_rc 0 "a full stop exits 0"
expect_summary "🌙 Staging stopped" "a full stop still reports a full stop"
expect_summary "available → stopping" "the RDS row reports the transition that was made"
expect_not_summary "::warning" "no warning annotation on a clean stop"

# A failed ECS stop exits 1, and the headline must not say "Staging stopped".
up_world
stag_env
export STUB_FAIL_ON="update-service"
summary_run stop
expect_rc 1 "a failed ECS stop fails the job"
expect_summary "Staging stop failed" "the headline reports the failure"
expect_not_summary "🌙 Staging stopped" "and does not claim a full stop"

down_world
stag_env
summary_run stop
expect_summary "already at desired count 0" "a service already at 0 is reported as such, not as a change"
expect_summary "already stopped" "an already stopped resource is reported as such"

echo ""
echo "==== ${PASS} passed, ${FAIL} failed ===="
[[ "${FAIL}" -eq 0 ]]
