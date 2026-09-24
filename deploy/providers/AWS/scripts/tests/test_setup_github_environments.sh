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
# `gh` and `aws` are stubs first on PATH, and every call to either is logged. The
# `aws` stub answers from environment variables (STUB_*), so each case describes
# the account it runs against: which bucket it owns, which roles and boundary
# exist, and what their trust policies say. Every failure case runs WITHOUT
# --dry-run and asserts the gh log is empty — the script's promise is that a
# wrong account, a missing role or a mismatched trust policy stops it before its
# first write, and a dry run would prove nothing about that.
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

# AWS's documentation placeholder, so the "never printed" assertion has something
# to look for without a real account ID in a tracked file.
ACCOUNT="111122223333"
CALLS="${TEST_ROOT}/calls.log"

mkdir -p "${TEST_ROOT}/bin"
cat >"${TEST_ROOT}/bin/gh" <<'STUB'
#!/usr/bin/env bash
echo "gh $*" >>"${CALLS}"
cat >/dev/null
STUB
cat >"${TEST_ROOT}/bin/aws" <<'STUB'
#!/usr/bin/env bash
echo "aws $*" >>"${CALLS}"
[[ -z "${AWS_ACCESS_KEY_ID:-}${AWS_SESSION_TOKEN:-}" ]] || { echo "static AWS_* credentials leaked into the call" >&2; exit 97; }
arg() { local flag="$1"; shift; while [[ $# -gt 0 ]]; do [[ "$1" == "${flag}" ]] && { echo "$2"; return; }; shift; done; }
no_such_entity() { echo "An error occurred (NoSuchEntity) when calling the $1 operation: not found" >&2; exit 254; }
case "$1 $2" in
    "sts get-caller-identity")
        [[ -z "${STUB_STS_FAIL:-}" ]] || { echo "Error when retrieving token from sso: Token has expired" >&2; exit 255; }
        printf '%s\t%s\n' "${STUB_ACCOUNT}" "arn:aws:sts::${STUB_ACCOUNT}:assumed-role/Admin/operator" ;;
    "s3api head-bucket")
        [[ "$(arg --bucket "$@")" == "${STUB_BUCKET}" && "$(arg --expected-bucket-owner "$@")" == "${STUB_ACCOUNT}" ]] ||
            { echo "An error occurred (403) when calling the HeadBucket operation: Forbidden" >&2; exit 254; } ;;
    "iam get-role")
        name="$(arg --role-name "$@")"
        [[ " ${STUB_MISSING_ROLES:-} " != *" ${name} "* ]] || no_such_entity GetRole
        case "$(arg --query "$@")" in
            Role.Arn) echo "arn:aws:iam::${STUB_ACCOUNT}:role/${name}-ARNMARKER" ;;
            *":sub"*) echo "${STUB_SUB}" ;;
            *":job_workflow_ref"*)
                if [[ "${name}" == *Apply* ]]; then echo "${STUB_APPLY_REF}"; else echo "None"; fi ;;
            *) echo "unexpected get-role query" >&2; exit 98 ;;
        esac ;;
    "iam get-policy")
        [[ -z "${STUB_BOUNDARY_MISSING:-}" ]] || no_such_entity GetPolicy
        [[ "$(arg --policy-arn "$@")" == "arn:aws:iam::${STUB_ACCOUNT}:policy/AICentre-FLIPTerraformBoundary" ]] ||
            no_such_entity GetPolicy
        echo "AICentre-FLIPTerraformBoundary" ;;
    *) echo "unexpected aws call: $*" >&2; exit 98 ;;
esac
STUB
chmod +x "${TEST_ROOT}/bin/gh" "${TEST_ROOT}/bin/aws"

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
FLIP_TFSTATE_BUCKET_NAME=flip-terraform-state-stag
ENVFILE

STAG_SUB="repo:acme/flip:environment:aws-stag"
STAG_REF="acme/flip/.github/workflows/terraform_apply.yml@refs/heads/develop"

# run_case <title> [VAR=value …] -- <script args…>
# The account defaults to a correctly bootstrapped staging one; a case overrides
# what it is about.
run_case() {
    local title="$1"
    shift
    local overrides=()
    while [[ "$1" != "--" ]]; do
        overrides+=("$1")
        shift
    done
    shift
    echo ""
    echo "-- ${title}"
    : >"${CALLS}"
    STDOUT="$(env -i \
        PATH="${TEST_ROOT}/bin:${PATH}" \
        HOME="${TEST_ROOT}" \
        CALLS="${CALLS}" \
        AWS_ACCESS_KEY_ID="STATICKEYMARKER" \
        AWS_SESSION_TOKEN="STATICTOKENMARKER" \
        STUB_ACCOUNT="${ACCOUNT}" \
        STUB_BUCKET="flip-terraform-state-stag" \
        STUB_SUB="${STAG_SUB}" \
        STUB_APPLY_REF="${STAG_REF}" \
        "${overrides[@]}" \
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

expect_no_gh() {
    if grep -q '^gh ' "${CALLS}"; then
        no "gh was never called" "$(grep '^gh ' "${CALLS}" | head -3)"
    else
        ok "gh was never called"
    fi
}

# A refused run: non-zero, no GitHub write, no account ID in the output.
expect_refused_before_gh() {
    expect_rc 1 "$1"
    expect_mentions "$2" "$3"
    expect_no_gh
    expect_silent_about "${ACCOUNT}" "the account ID is not printed"
}

echo "==== setup-github-environments.sh ===="

# 1. THE REGRESSION. Both ends of REQUIRED_KEYS must be classified as required: a
#    membership glob that only matches the final element reports every other
#    required key as an optional absence, which is the opposite of this script's
#    job. AWS_REGION is first in the list, LOCAL_TRUST_PUBLIC_IPS last.
run_case "a partial env file is read in dry-run" -- \
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
#    env file's values nor the ARNs read from IAM may reach the output.
expect_silent_about "SECRETVALUEMARKER" "a secret value is not printed"
expect_silent_about "service-key-marker" "nor is a variable's value"
expect_silent_about "ARNMARKER" "nor a role ARN read from IAM"
expect_silent_about "${ACCOUNT}" "nor the account ID"
expect_no_gh
expect_mentions "nothing was written" "the dry run says so"

# 4. The mode reaches the environment as TF_PROD, from the same token the workflows
#    read — the two cannot disagree if only this one sets it.
expect_mentions "set TF_PROD=stag" "the mode is written as TF_PROD"

# 5. Prod-only asymmetry: an empty demo bucket on prod is a gap, not an omission
#    (cloudfront.tf gates four objects on it being non-empty). The env file is the
#    same one case 1 used, where the same key was optional — the difference is the
#    mode alone.
run_case "prod treats an absent demo bucket as required" \
    STUB_SUB="repo:acme/flip:environment:aws-prod" \
    STUB_APPLY_REF="acme/flip/.github/workflows/terraform_apply.yml@refs/heads/main" -- \
    --mode true --env-file "${ENV_FILE}" --repo acme/flip --dry-run
expect_rc 0 "dry run succeeds"
expect_mentions "- DEMO_ASSETS_BUCKET_NAME" "the demo bucket is required on prod"
expect_mentions "branch policy: main only" "and prod's branch policy is stated"

# 6. The LZA modes require their extra keys, appended to the same array — the
#    classification has to survive the append, not just the base list.
run_case "lza modes require the LZA keys" -- \
    --mode lza-stag --env-file "${ENV_FILE}" --repo acme/flip --dry-run
expect_rc 0 "dry run succeeds"
expect_mentions "- NETWORKING_INGRESS_CIDRS" "an LZA key is required"
expect_mentions "- AWS_REGION" "the base list is still classified as required"
expect_mentions "- EFS_PROVISION_IMAGE" "all six LZA keys, not only the last one"
expect_mentions "set TF_PROD=lza-stag" "and the LZA token is what TF_PROD gets"

# 7. Failures an operator has to get right. Nothing here may be a warning.
run_case "the old flag is refused with the new name" -- \
    --env stag --env-file "${ENV_FILE}" --dry-run
expect_rc 1 "refused"
expect_mentions "--env is now --mode" "and points at --mode"

run_case "an unknown mode is refused" -- \
    --mode prod --env-file "${ENV_FILE}" --dry-run
expect_rc 1 "refused"
expect_mentions "--mode must be one of" "listing the four tokens"

run_case "a missing env file is refused" -- \
    --mode stag --env-file "${TEST_ROOT}/nope.env" --dry-run
expect_rc 1 "refused"
expect_mentions "--env-file must point at an existing file" "naming the flag"

# 8. The AWS side. Every one of these runs for real (no --dry-run): the check has to
#    stop the script before its first GitHub write, not merely before the end.
run_case "an expired session is refused, with the login command" STUB_STS_FAIL=1 -- \
    --mode stag --env-file "${ENV_FILE}" --repo acme/flip
expect_refused_before_gh "refused" "aws sso login --profile stag" "with the command that fixes it"

run_case "a state bucket in another account is refused" STUB_BUCKET="flip-terraform-state-prod" -- \
    --mode stag --env-file "${ENV_FILE}" --repo acme/flip
expect_refused_before_gh "refused" "is not in the" "naming the env-file / profile mismatch"
expect_mentions "flip-terraform-state-stag" "and the bucket the env file named"

ENV_NO_BUCKET="${TEST_ROOT}/no-bucket.env"
grep -v '^FLIP_TFSTATE_BUCKET_NAME=' "${ENV_FILE}" >"${ENV_NO_BUCKET}"
run_case "an env file without the state bucket is refused" -- \
    --mode stag --env-file "${ENV_NO_BUCKET}" --repo acme/flip
expect_refused_before_gh "refused" "FLIP_TFSTATE_BUCKET_NAME is not set" "naming the key"

run_case "a missing apply role is refused" STUB_MISSING_ROLES="AICentre-FLIPTerraformApplyRole" -- \
    --mode stag --env-file "${ENV_FILE}" --repo acme/flip
expect_refused_before_gh "refused" "AICentre-FLIPTerraformApplyRole does not exist" "naming the role"
expect_mentions "has not applied" "and saying who has not bootstrapped the account"
expect_mentions "aicentre-lza-iac" "naming the platform repositories"

run_case "a missing plan role is refused" STUB_MISSING_ROLES="AICentre-FLIPTerraformPlanRole" -- \
    --mode stag --env-file "${ENV_FILE}" --repo acme/flip
expect_refused_before_gh "refused" "AICentre-FLIPTerraformPlanRole does not exist" "naming the role"

run_case "the role names can be overridden" TF_APPLY_ROLE_NAME="Custom-ApplyRole" \
    STUB_MISSING_ROLES="Custom-ApplyRole" -- \
    --mode stag --env-file "${ENV_FILE}" --repo acme/flip
expect_refused_before_gh "refused" "Custom-ApplyRole does not exist" "the override is the name looked up"

run_case "an apply role pinned to the other branch is refused" \
    STUB_APPLY_REF="acme/flip/.github/workflows/terraform_apply.yml@refs/heads/main" -- \
    --mode stag --env-file "${ENV_FILE}" --repo acme/flip
expect_refused_before_gh "refused" "@refs/heads/develop" "naming the ref stag needs"
expect_mentions "job_workflow_ref" "and the claim that disagrees"

run_case "roles trusting the other environment are refused" STUB_SUB="repo:acme/flip:environment:aws-prod" -- \
    --mode stag --env-file "${ENV_FILE}" --repo acme/flip
expect_refused_before_gh "refused" "repo:acme/flip:environment:aws-stag" "naming the sub stag needs"

run_case "prod roles are refused for a stag mode even on the right account" \
    STUB_SUB="repo:acme/flip:environment:aws-prod" \
    STUB_APPLY_REF="acme/flip/.github/workflows/terraform_apply.yml@refs/heads/main" -- \
    --mode lza-stag --env-file "${ENV_FILE}" --repo acme/flip
expect_refused_before_gh "refused" "aws-stag" "the LZA staging token is held to aws-stag too"

run_case "roles trusting another repository are refused" -- \
    --mode stag --env-file "${ENV_FILE}" --repo someone-else/flip
expect_refused_before_gh "refused" "repo:someone-else/flip:environment:aws-stag" "naming the repository's sub"

run_case "a missing permissions boundary is refused" STUB_BOUNDARY_MISSING=1 -- \
    --mode stag --env-file "${ENV_FILE}" --repo acme/flip
expect_refused_before_gh "refused" "AICentre-FLIPTerraformBoundary does not exist" "naming the boundary"

# 9. And the other side of "before": a run that passes every check does write, and
#    only after the last AWS call — otherwise the empty-log assertions above could
#    be satisfied by a script that never calls gh at all.
run_case "a verified real run writes, after every AWS check" -- \
    --mode stag --env-file "${ENV_FILE}" --repo acme/flip
expect_rc 0 "succeeds"
last_aws="$(grep -n '^aws ' "${CALLS}" | tail -1 | cut -d: -f1)"
first_gh="$(grep -n '^gh ' "${CALLS}" | head -1 | cut -d: -f1)"
if [[ -n "${first_gh}" && -n "${last_aws}" && "${first_gh}" -gt "${last_aws}" ]]; then
    ok "every gh call comes after the last aws call"
else
    no "every gh call comes after the last aws call" "$(cat "${CALLS}")"
fi
if grep -q '^gh variable set TF_APPLY_ROLE_ARN --env aws-stag --repo acme/flip$' "${CALLS}"; then
    ok "the apply role ARN is written to aws-stag"
else
    no "the apply role ARN is written to aws-stag" "$(grep '^gh ' "${CALLS}")"
fi
if ! grep -q 'STATIC' "${CALLS}" && [[ "${STDERR}" != *"leaked"* ]]; then
    ok "static AWS_* credentials never reach an aws call"
else
    no "static AWS_* credentials never reach an aws call"
fi
expect_silent_about "${ACCOUNT}" "the account ID is not printed"

echo ""
echo "==== ${PASS} passed, ${FAIL} failed ===="
[[ "${FAIL}" -eq 0 ]]
