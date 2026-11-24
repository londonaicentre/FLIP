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

# Black-box tests for scripts/reset-fl-server-local.sh — what `make rollback-centralhub` runs before it
# repoints a service, so that an fl-server image older than FLIP#1390 does not find
# local/flip__p_resources.json on EFS and fail to load its configuration.
#
# Drives the REAL script with `aws` stubbed on PATH (no credentials, no network). The stub answers each
# ECS call from a fixture file and records every call, so a test can see whether the task ran and how.
#
# The invariant under test: the script exits 0 only when the efs-provision-certs task ran and exited 0,
# or when the environment has no such task (no EFS, nothing persistent to reset). Anything else must fail,
# because the rollback relies on it to stop before it moves a service.
#
# Usage:
#     bash deploy/providers/AWS/scripts/tests/test_reset_fl_server_local.sh

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$(cd "${HERE}/.." && pwd)/reset-fl-server-local.sh"

TEST_ROOT="$(mktemp -d)"
trap 'rm -rf "${TEST_ROOT}"' EXIT

MOCKBIN="${TEST_ROOT}/mockbin"
mkdir -p "${MOCKBIN}"

# aws: the five ECS calls the script makes. Every call is appended to ${FIXTURE_DIR}/calls, one per line.
#   ${FIXTURE_DIR}/families     — list-task-definition-families output (tab-separated, as --output text)
#   ${FIXTURE_DIR}/network      — describe-services networkConfiguration JSON ("null" when the service has none)
#   ${FIXTURE_DIR}/task-arn     — run-task's tasks[0].taskArn ("None" when ECS started nothing)
#   ${FIXTURE_DIR}/exit-code    — describe-tasks' exit code for the task's own container, selected by name
#   ${FIXTURE_DIR}/sidecar-exit-code — what selecting containers[0] reads instead: GuardDuty Runtime Monitoring's
#                                 sidecar, which often sorts first (AWS AGENTS.md, "Verifying a Central-Hub FL redeploy")
#   ${FIXTURE_DIR}/fail-<call>  — present: that call fails the way an API error does
cat >"${MOCKBIN}/aws" <<'MOCK_AWS'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"${FIXTURE_DIR}/calls"
call="$2"
if [[ -e "${FIXTURE_DIR}/fail-${call}" ]]; then
    echo "An error occurred (AccessDeniedException) when calling ${call}" >&2
    exit 254
fi
case "${call}" in
    list-task-definition-families) cat "${FIXTURE_DIR}/families" ;;
    describe-services) cat "${FIXTURE_DIR}/network" ;;
    run-task) cat "${FIXTURE_DIR}/task-arn" ;;
    wait) ;;
    describe-tasks)
        case "$*" in
            *"containers[?name=='provision-efs-certs']"*) cat "${FIXTURE_DIR}/exit-code" ;;
            *"containers[0]"*) cat "${FIXTURE_DIR}/sidecar-exit-code" ;;
            *) echo "unexpected describe-tasks query: $*" >&2; exit 1 ;;
        esac ;;
    *) echo "unexpected aws call: $*" >&2; exit 1 ;;
esac
MOCK_AWS
chmod +x "${MOCKBIN}/aws"

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

NETWORK='{"awsvpcConfiguration":{"subnets":["subnet-a"],"securityGroups":["sg-fl"],"assignPublicIp":"DISABLED"}}'
TASK_ARN="arn:aws:ecs:eu-west-2:000000000000:task/flip-cluster/0123abcd"

# A fixture for a healthy run: the task family exists, the fl-server has a network, the task exits 0.
fixture() {
    FIXTURE_DIR="${TEST_ROOT}/fx-${RANDOM}${RANDOM}"
    mkdir -p "${FIXTURE_DIR}"
    printf 'efs-provision-certs\n' >"${FIXTURE_DIR}/families"
    printf '%s\n' "${NETWORK}" >"${FIXTURE_DIR}/network"
    printf '%s\n' "${TASK_ARN}" >"${FIXTURE_DIR}/task-arn"
    printf '0\n' >"${FIXTURE_DIR}/exit-code"
    printf '143\n' >"${FIXTURE_DIR}/sidecar-exit-code"
    : >"${FIXTURE_DIR}/calls"
    export FIXTURE_DIR
}

run_reset() {
    echo ""
    echo "-- $1"
    shift
    STDOUT="$(env PATH="${MOCKBIN}:${PATH}" bash "${SCRIPT}" "$@" 2>"${TEST_ROOT}/err")"
    RC=$?
    STDERR="$(cat "${TEST_ROOT}/err")"
    CALLS="$(cat "${FIXTURE_DIR}/calls")"
}

expect_rc() {
    if [[ "${RC}" -eq "$1" ]]; then
        ok "$2"
    else
        no "$2" "wanted exit $1, got ${RC}" "stdout: ${STDOUT}" "stderr: ${STDERR}"
    fi
}

expect_call() {
    if printf '%s\n' "${CALLS}" | grep -Fq -- "$1"; then
        ok "$2"
    else
        no "$2" "no aws call containing: $1" "calls: ${CALLS}"
    fi
}

expect_no_call() {
    if printf '%s\n' "${CALLS}" | grep -Fq -- "$1"; then
        no "$2" "unexpected aws call containing: $1" "calls: ${CALLS}"
    else
        ok "$2"
    fi
}

expect_text() {
    if printf '%s\n%s\n' "${STDOUT}" "${STDERR}" | grep -Fq -- "$1"; then
        ok "$2"
    else
        no "$2" "output lacks: $1" "stdout: ${STDOUT}" "stderr: ${STDERR}"
    fi
}

echo "==== reset-fl-server-local.sh ===="

# 1. THE ROLLBACK CASE. The task runs in the fl-server's network, is waited for, and its exit code read.
fixture
run_reset "the provisioning task runs where the fl-server does and exits 0" flip-cluster fl-server-net-1
expect_rc 0 "succeeds"
expect_call "ecs run-task --cluster flip-cluster --task-definition efs-provision-certs --launch-type FARGATE" \
    "runs the efs-provision-certs task on Fargate in the hub's cluster"
expect_call "--network-configuration ${NETWORK}" "in the fl-server's subnets and security group"
expect_call "ecs describe-services --cluster flip-cluster --services fl-server-net-1" \
    "reads the network from the fl-server service"
expect_call "ecs wait tasks-stopped --cluster flip-cluster --tasks ${TASK_ARN}" "waits for the task to stop"
expect_call "ecs describe-tasks --cluster flip-cluster --tasks ${TASK_ARN}" "reads its exit code"

# 2. A task that fails leaves local/ in an unknown state: the rollback must not go ahead.
fixture
printf '1\n' >"${FIXTURE_DIR}/exit-code"
run_reset "the provisioning task exits non-zero" flip-cluster fl-server-net-1
expect_rc 1 "fails"
expect_text "exited with code 1" "says how the task ended"

# 2b. GuardDuty's sidecar sorts first and exits 0 while the task fails: the task's own exit code decides.
fixture
printf '1\n' >"${FIXTURE_DIR}/exit-code"
printf '0\n' >"${FIXTURE_DIR}/sidecar-exit-code"
run_reset "the provisioning task fails beside a GuardDuty sidecar that exits 0" flip-cluster fl-server-net-1
expect_rc 1 "fails"
expect_text "exited with code 1" "reports the task's own exit code"

# 3. No EFS, no task definition: local/ is not persistent, so there is nothing to reset.
fixture
printf 'efs-provision-certs-legacy\tflip-api\n' >"${FIXTURE_DIR}/families"
run_reset "no efs-provision-certs family (a longer name sharing the prefix does not count)" \
    flip-cluster fl-server-net-1
expect_rc 0 "succeeds"
expect_no_call "run-task" "runs no task"
expect_text "keeps no fl-server local/ on EFS" "says why nothing was done"

# 4. Not being able to tell whether the task exists is not the same as it not existing.
fixture
: >"${FIXTURE_DIR}/fail-list-task-definition-families"
run_reset "listing the task families fails" flip-cluster fl-server-net-1
expect_rc 1 "fails"
expect_no_call "run-task" "runs no task"

# 5. Without the fl-server's network the task cannot run where Terraform runs it.
fixture
printf 'null\n' >"${FIXTURE_DIR}/network"
run_reset "the fl-server service has no network configuration" flip-cluster fl-server-net-1
expect_rc 1 "fails"
expect_no_call "run-task" "runs no task"

# 6. run-task reports placement failures in its body and still exits 0.
fixture
printf 'None\n' >"${FIXTURE_DIR}/task-arn"
run_reset "ECS starts no task" flip-cluster fl-server-net-1
expect_rc 1 "fails"
expect_text "started no efs-provision-certs task" "says no task started"

# 7. The waiter gives up after ten minutes; the task's outcome is then unknown.
fixture
: >"${FIXTURE_DIR}/fail-wait"
run_reset "the task does not stop before the wait gives up" flip-cluster fl-server-net-1
expect_rc 1 "fails"
expect_no_call "describe-tasks" "does not read an exit code from a task that may still be running"

# 8. Both arguments are required.
fixture
run_reset "called without the service" flip-cluster
expect_rc 1 "fails"
expect_no_call "ecs" "makes no aws call"

# 9. The container the script reads is the one Terraform defines in the efs-provision-certs family.
echo ""
echo "-- the container name matches ecs_efs_provision.tf"
container="$(sed -n 's/^container="\(.*\)"$/\1/p' "${SCRIPT}")"
if [[ -n "${container}" ]] && grep -Fq "name = \"${container}\"" "${HERE}/../../ecs_efs_provision.tf"; then
    ok "${container} is the task definition's container"
else
    no "the script's container ('${container}') is not a container in ecs_efs_provision.tf"
fi

echo ""
echo "==== ${PASS} passed, ${FAIL} failed ===="
[[ "${FAIL}" -eq 0 ]]
