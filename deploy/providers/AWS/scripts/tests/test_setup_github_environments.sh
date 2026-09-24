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

# Black-box tests for scripts/setup-github-environments.sh — the script a repo
# admin runs to populate aws-stag / aws-prod (FLIP#962, FLIP#1199).
#
# Everything runs in --dry-run against a *partial* env file and a seeded `ci/`
# backend state, with `gh` and `terraform` stubs first on PATH. The `gh` stub exits
# 99 if it is called at all: the script is `set -euo pipefail`, so any case that
# reports success has proven that a dry run wrote nothing — the assertion is the
# exit status, not a reading of the dry-run branches.
#
# The partial env file is the point of the fixture: which absent keys are reported
# as REQUIRED comes from a membership test against the lists in compose-ci-env.sh,
# and that test is a glob. Getting the glob wrong does not fail loudly — it
# reclassifies real required keys as optional, and the operator is told a key the
# workflow will dereference is "fine to omit". So the fixture omits one key from
# the middle of REQUIRED_KEYS (AWS_REGION) and one from its end
# (LOCAL_TRUST_PUBLIC_IPS), and asserts both.
#
# Usage:
#     bash deploy/providers/AWS/scripts/tests/test_setup_github_environments.sh

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$(cd "${HERE}/.." && pwd)/setup-github-environments.sh"

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

# --- stubs, fixtures -------------------------------------------------------------

mkdir -p "${TEST_ROOT}/bin"
cat >"${TEST_ROOT}/bin/gh" <<'STUB'
#!/usr/bin/env bash
echo "gh was called: $*" >&2
exit 99
STUB
# ci_output runs `terraform output -raw <name>`; the values carry a marker so the
# "no value is printed" case can look for it.
cat >"${TEST_ROOT}/bin/terraform" <<'STUB'
#!/usr/bin/env bash
case "${3:-}" in
    plan_role_arn) echo "arn:aws:iam::000000000000:role/PLANROLEMARKER" ;;
    apply_role_arn) echo "arn:aws:iam::000000000000:role/APPLYROLEMARKER" ;;
esac
STUB
chmod +x "${TEST_ROOT}/bin/gh" "${TEST_ROOT}/bin/terraform"

BACKEND_STATE="${TEST_ROOT}/terraform.tfstate"
printf '{"backend":{"type":"s3","config":{"bucket":"flip-terraform-state-stag","key":"ci/terraform.tfstate"}}}\n' \
    >"${BACKEND_STATE}"

# A file an operator really might have: the keys they needed last time, missing
# the ones being asserted. Values are markers, never real ones.
ENV_FILE="${TEST_ROOT}/partial.env"
cat >"${ENV_FILE}" <<'ENVFILE'
VPC_NAME=flip-vpc
AICENTRE_BUCKET_NAME=aicentre-stag
POSTGRES_DB=flip
ADMIN_USER_PASSWORD=SECRETVALUEMARKER
AES_KEY_BASE64=bm90LWEtcmVhbC1rZXk=
INTERNAL_SERVICE_KEY=service-key-marker
ENVFILE

run_case() {
    local title="$1"
    shift
    echo ""
    echo "-- ${title}"

    STDOUT="$(env -i \
        PATH="${TEST_ROOT}/bin:${PATH}" \
        HOME="${TEST_ROOT}" \
        CI_BACKEND_STATE="${BACKEND_STATE}" \
        CI_STATE_BUCKET="flip-terraform-state-stag" \
        bash "${SCRIPT}" "$@" 2>"${TEST_ROOT}/err")"
    RC=$?
    STDERR="$(cat "${TEST_ROOT}/err")"
}

expect_rc() {
    local want="$1" what="$2"
    if [[ "${RC}" -eq "${want}" ]]; then
        ok "${what} (exit ${want})"
    else
        no "${what}" "wanted exit ${want}, got ${RC}" "stdout: ${STDOUT}" "stderr: ${STDERR}"
    fi
}

expect_mentions() {
    local needle="$1" what="$2"
    if [[ "${STDOUT}${STDERR}" == *"${needle}"* ]]; then
        ok "${what}"
    else
        no "${what}" "output did not mention: ${needle}"
    fi
}

expect_silent_about() {
    local needle="$1" what="$2"
    if [[ "${STDOUT}${STDERR}" == *"${needle}"* ]]; then
        no "${what}" "output mentioned: ${needle}"
    else
        ok "${what}"
    fi
}

echo "==== setup-github-environments.sh ===="

# 1. THE REGRESSION. Both ends of REQUIRED_KEYS must be classified as required: a
#    membership glob that only matches the final element reports every other
#    required key as an optional absence, which is the opposite of this script's
#    job. AWS_REGION is first in the list, LOCAL_TRUST_PUBLIC_IPS last.
run_case "a partial env file is read in dry-run" \
    --mode stag --env-file "${ENV_FILE}" --repo acme/flip --dry-run
expect_rc 0 "dry run succeeds (and never called gh)"
expect_mentions "- AWS_REGION" "a required key in the middle of the list is reported"
expect_mentions "- LOCAL_TRUST_PUBLIC_IPS" "and so is the one at the end"
expect_mentions "REQUIRED but absent" "under the required heading"
expect_mentions "reconcile_ci_env.py --env stag" "with the recovery command for this mode"

# 2. A key that is legitimately optional is reported as optional, not required —
#    the fix must not turn the list into "everything absent is required".
expect_mentions "optional key(s) absent" "optional absences are grouped separately"
expect_mentions "JOB_RESOURCE_SPEC_NUM_GPUS" "an optional absent key is named there"
expect_silent_about "- JOB_RESOURCE_SPEC_NUM_GPUS" "and not in the required list"
expect_mentions "skip ENFORCE_MFA" "ENFORCE_MFA is skipped, not reported"
# ...and the demo bucket is optional on stag, where no public Ark+ demo is hosted.
expect_silent_about "- DEMO_ASSETS_BUCKET_NAME" "an absent demo bucket is not required on stag"

# 3. The header's promise: key names only. Values arrive on stdin, so neither the
#    env file's values nor the ARNs read out of ci/ state may reach the output.
expect_silent_about "SECRETVALUEMARKER" "a secret value is not printed"
expect_silent_about "service-key-marker" "nor is a variable's value"
expect_silent_about "PLANROLEMARKER" "nor an ARN read from ci/ state"
expect_silent_about "APPLYROLEMARKER" "for either role"
expect_mentions "nothing was written" "the dry run says so"

# 4. The mode reaches the environment as TF_PROD, from the same token the workflows
#    read — the two cannot disagree if only this one sets it.
expect_mentions "set TF_PROD=stag" "the mode is written as TF_PROD"

# 5. Prod-only asymmetry: an empty demo bucket on prod is a gap, not an omission
#    (cloudfront.tf gates four objects on it being non-empty). The env file is the
#    same one case 1 used, where the same key was optional — the difference is the
#    mode alone.
run_case "prod treats an absent demo bucket as required" \
    --mode true --env-file "${ENV_FILE}" --repo acme/flip --dry-run
expect_rc 0 "dry run succeeds"
expect_mentions "- DEMO_ASSETS_BUCKET_NAME" "the demo bucket is required on prod"
expect_mentions "branch policy: main only" "and prod's branch policy is stated"

# 6. The LZA modes require their extra keys, appended to the same array — the
#    classification has to survive the append, not just the base list.
run_case "lza modes require the LZA keys" \
    --mode lza-stag --env-file "${ENV_FILE}" --repo acme/flip --dry-run
expect_rc 0 "dry run succeeds"
expect_mentions "- NETWORKING_INGRESS_CIDRS" "an LZA key is required"
expect_mentions "- AWS_REGION" "the base list is still classified as required"
expect_mentions "- EFS_PROVISION_IMAGE" "all six LZA keys, not only the last one"
expect_mentions "set TF_PROD=lza-stag" "and the LZA token is what TF_PROD gets"

# 7. Failures an operator has to get right. Nothing here may be a warning.
run_case "the old flag is refused with the new name" \
    --env stag --env-file "${ENV_FILE}" --dry-run
expect_rc 1 "refused"
expect_mentions "--env is now --mode" "and points at --mode"

run_case "an unknown mode is refused" \
    --mode prod --env-file "${ENV_FILE}" --dry-run
expect_rc 1 "refused"
expect_mentions "--mode must be one of" "listing the four tokens"

run_case "a missing env file is refused" \
    --mode stag --env-file "${TEST_ROOT}/nope.env" --dry-run
expect_rc 1 "refused"
expect_mentions "--env-file must point at an existing file" "naming the flag"

# The seeded state is what makes case 1 pass, so the same run without it must stop
# where the missing file is, not halfway through a half-populated environment.
STDOUT="$(env -i PATH="${TEST_ROOT}/bin:${PATH}" HOME="${TEST_ROOT}" \
    CI_BACKEND_STATE="${TEST_ROOT}/missing.tfstate" CI_STATE_BUCKET="flip-terraform-state-stag" \
    bash "${SCRIPT}" --mode stag --env-file "${ENV_FILE}" --dry-run 2>"${TEST_ROOT}/err")"
RC=$?
STDERR="$(cat "${TEST_ROOT}/err")"
if [[ "${RC}" -eq 1 && "${STDOUT}${STDERR}" == *"has not been initialised"* ]]; then
    ok "an uninitialised ci/ is refused"
else
    no "an uninitialised ci/ is refused" "exit ${RC}" "stdout: ${STDOUT}" "stderr: ${STDERR}"
fi
expect_mentions "make -C ci init" "with the command that fixes it"

# 8. The wrong-account guard: ci/ is one working directory re-pointed between
#    accounts, and reading it while it is bound elsewhere would wire this
#    environment to the other account's roles.
STDOUT="$(env -i PATH="${TEST_ROOT}/bin:${PATH}" HOME="${TEST_ROOT}" \
    CI_BACKEND_STATE="${BACKEND_STATE}" CI_STATE_BUCKET="flip-terraform-state-production" \
    bash "${SCRIPT}" --mode stag --env-file "${ENV_FILE}" --dry-run 2>"${TEST_ROOT}/err")"
RC=$?
STDERR="$(cat "${TEST_ROOT}/err")"
if [[ "${RC}" -eq 1 && "${STDOUT}${STDERR}" == *"is initialised for"* ]]; then
    ok "a ci/ bound to another account is refused"
else
    no "a ci/ bound to another account is refused" "exit ${RC}" "stdout: ${STDOUT}" "stderr: ${STDERR}"
fi

echo ""
echo "==== ${PASS} passed, ${FAIL} failed ===="
[[ "${FAIL}" -eq 0 ]]
