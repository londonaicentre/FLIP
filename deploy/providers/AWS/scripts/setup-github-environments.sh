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

# Create and populate the GitHub environment the Terraform CI workflows read
# (FLIP#962): aws-stag or aws-prod.
#
# Run by a repo admin, from a machine that already has the environment's values.
# Nothing is printed except key names, in --dry-run as well as a real run: values
# are piped to `gh` on stdin, never passed as an argument, so they appear neither
# in this script's output nor in the process table.
#
# --mode takes the deploy/env_mode.mk PROD token — stag | true | lza-stag | lza —
# the same value the workflow is given as its TF_PROD variable. It selects the
# GitHub environment (aws-stag / aws-prod), the local AWS profile those roles are
# reached through, the state bucket, and which keys the manifest requires. The
# environment *names* do not change with it: a repointed estate reuses aws-stag /
# aws-prod, which is what keeps one CI pipeline covering both the self-contained
# and the platform-managed accounts (README, "Repointing CI at the LZA accounts").
#
# The secret-vs-variable split is not hard-coded here. It is read out of
# .github/workflows/terraform_plan.yml, because that workflow is what actually
# dereferences them: a key the workflow reads as `secrets.X` but that was stored
# as a variable resolves to empty, and compose-ci-env.sh then fails the run with
# a missing-key error that points at the wrong thing. Deriving the split removes
# the chance of that disagreeing.
#
# Before it writes anything to GitHub — in --dry-run too — it checks the AWS side
# under the mode's profile: that the env file's state bucket belongs to the
# account the profile reaches (so the env file and the profile are the same
# estate), that the plan and apply roles exist there, that their trust policies
# name this repository's environment and, for apply, this mode's branch, and that
# the permissions boundary exists. The role ARNs are read from IAM, not from a
# local Terraform state. Any failure stops the run before the first `gh` call.
#
# Idempotent: re-running updates values in place.
#
# Usage:
#     scripts/setup-github-environments.sh --mode stag --env-file ../../../.env.stag
#     scripts/setup-github-environments.sh --mode lza-stag --env-file ../../../.env.lza-stag --dry-run
#     scripts/setup-github-environments.sh --mode true --env-file ../../../.env.production
#
# Overrides: AWS_PROFILE_FOR_ENV (the profile for this mode), TF_PLAN_ROLE_NAME /
# TF_APPLY_ROLE_NAME / TF_BOUNDARY_NAME (default: the AICentre-FLIPTerraform* names
# the bootstrap module creates).

set -euo pipefail

die() {
    echo "❌ $*" >&2
    exit 1
}

REPO="${REPO:-londonaicentre/FLIP}"
MODE=""
ENV_FILE=""
DRY_RUN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --mode) MODE="$2"; shift 2 ;;
        --env-file) ENV_FILE="$2"; shift 2 ;;
        --repo) REPO="$2"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        --env) die "--env is now --mode. Pass the deploy/env_mode.mk PROD token: stag | true | lza-stag | lza" ;;
        *) die "unknown argument: $1" ;;
    esac
done

[[ -n "${MODE}" ]] || die "usage: $0 --mode stag|true|lza-stag|lza --env-file <path> [--dry-run]"
case "${MODE}" in
    stag) ENV=stag; IS_LZA=0 ;;
    true) ENV=prod; IS_LZA=0 ;;
    lza-stag) ENV=stag; IS_LZA=1 ;;
    lza) ENV=prod; IS_LZA=1 ;;
    *) die "--mode must be one of stag, true, lza-stag, lza (got '${MODE}')" ;;
esac
[[ -n "${ENV_FILE}" && -f "${ENV_FILE}" ]] || die "--env-file must point at an existing file (got '${ENV_FILE}')"
command -v gh >/dev/null || die "gh CLI is required"
command -v aws >/dev/null || die "aws CLI is required"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AWS_DIR="$(cd "${HERE}/.." && pwd)"
REPO_ROOT="$(cd "${AWS_DIR}/../../.." && pwd)"
PLAN_WORKFLOW="${REPO_ROOT}/.github/workflows/terraform_plan.yml"
[[ -f "${PLAN_WORKFLOW}" ]] || die "cannot find ${PLAN_WORKFLOW} — the secret/variable split is read from it"

GH_ENV="aws-${ENV}"
# The profile the CI roles in this mode are reached through. Matches the
# Makefile's PROD_AWS_PROFILE / STAG_AWS_PROFILE / LZA_AWS_PROFILE /
# LZA_STAG_AWS_PROFILE convention; override with AWS_PROFILE_FOR_ENV if your
# local profile names differ.
case "${MODE}" in
    stag) PROFILE_DEFAULT="${STAG_AWS_PROFILE:-stag}" ;;
    true) PROFILE_DEFAULT="${PROD_AWS_PROFILE:-prod}" ;;
    lza-stag) PROFILE_DEFAULT="${LZA_STAG_AWS_PROFILE:-lza-stag}" ;;
    lza) PROFILE_DEFAULT="${LZA_AWS_PROFILE:-lza-prod}" ;;
esac
AWS_PROFILE_FOR_ENV="${AWS_PROFILE_FOR_ENV:-${PROFILE_DEFAULT}}"

# Set one secret or variable, with the value on stdin.
#
# `gh secret set KEY --body "${value}"` was the original form and it leaked twice
# over: run() echoes "$*", so --dry-run printed every production secret to the
# terminal — while this script's header, the summary line and the README all
# promise key names only, and the README tells operators to dry-run first against
# .env.production. Passing it as an argument also puts it in the process table for
# the life of the call. gh reads the value from stdin when --body is omitted.
set_gh() {
    local kind="$1" key="$2" value="$3"
    if ((DRY_RUN)); then
        echo "   [dry-run] gh ${kind} set ${key} --env ${GH_ENV} --repo ${REPO}  (value on stdin, not shown)"
        return 0
    fi
    printf '%s' "${value}" | gh "${kind}" set "${key}" --env "${GH_ENV}" --repo "${REPO}" >/dev/null
}

# Read the env file the way make does: last assignment wins, value verbatim.
declare -A VALUES=()
while IFS= read -r line; do
    [[ "${line}" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]] && VALUES["${BASH_REMATCH[1]}"]="${BASH_REMATCH[2]}"
done <"${ENV_FILE}"

# ---------------------------------------------------------------------------
# 0. The AWS side, verified before anything is written to GitHub
# ---------------------------------------------------------------------------
#
# The roles, the boundary and the state bucket are owned by the platform
# repositories (modules/terraform_ci_bootstrap; ci/ for an account you bootstrap
# yourself). This script only reads them. Account IDs are never printed: they
# are not secrets, but they do not belong in a terminal log that gets pasted.

TF_PLAN_ROLE_NAME="${TF_PLAN_ROLE_NAME:-AICentre-FLIPTerraformPlanRole}"
TF_APPLY_ROLE_NAME="${TF_APPLY_ROLE_NAME:-AICentre-FLIPTerraformApplyRole}"
TF_BOUNDARY_NAME="${TF_BOUNDARY_NAME:-AICentre-FLIPTerraformBoundary}"
case "${ENV}" in
    stag) APPLY_BRANCH=develop ;;
    prod) APPLY_BRANCH=main ;;
esac
EXPECTED_SUB="repo:${REPO}:environment:${GH_ENV}"
EXPECTED_APPLY_REF="${REPO}/.github/workflows/terraform_apply.yml@refs/heads/${APPLY_BRANCH}"
NOT_BOOTSTRAPPED="the platform repository (aicentre-iac / aicentre-lza-iac) has not applied
   terraform_ci_bootstrap in the account '${AWS_PROFILE_FOR_ENV}' reaches — or, outside AI Centre,
   deploy/providers/AWS/ci has not been applied there."

# Static AWS_* env vars outrank the profile in the CLI's credential chain, and a
# stale one yields InvalidClientTokenId — the same reason the Makefiles' `_TF`
# unsets them. The profile is passed explicitly: without it this would silently
# read whatever the default profile points at.
aws_q() {
    env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN -u AWS_SECURITY_TOKEN \
        AWS_PROFILE="${AWS_PROFILE_FOR_ENV}" aws "$@"
}

echo "🔎 Checking the ${MODE} account through profile '${AWS_PROFILE_FOR_ENV}'"

identity="$(aws_q sts get-caller-identity --query '[Account, Arn]' --output text 2>&1)" ||
    die "could not reach AWS with profile '${AWS_PROFILE_FOR_ENV}' — an expired SSO session is the usual cause:
   aws sso login --profile ${AWS_PROFILE_FOR_ENV}
   (set AWS_PROFILE_FOR_ENV if your profile for ${MODE} has another name)"
read -r ACCOUNT CALLER_ARN <<<"${identity}"
[[ "${ACCOUNT}" =~ ^[0-9]{12}$ ]] || die "unexpected sts get-caller-identity output for profile '${AWS_PROFILE_FOR_ENV}'"
PARTITION="$(cut -d: -f2 <<<"${CALLER_ARN}")"

# The one value that ties the env file to an account. Asking S3 to check the
# owner proves the env file and the profile describe the same estate: a stag env
# file with the prod profile, or the reverse, stops here.
STATE_BUCKET="${VALUES[FLIP_TFSTATE_BUCKET_NAME]:-}"
[[ -n "${STATE_BUCKET}" ]] || die "FLIP_TFSTATE_BUCKET_NAME is not set in ${ENV_FILE}"
aws_q s3api head-bucket --bucket "${STATE_BUCKET}" --expected-bucket-owner "${ACCOUNT}" >/dev/null 2>&1 ||
    die "the state bucket '${STATE_BUCKET}' (FLIP_TFSTATE_BUCKET_NAME in ${ENV_FILE}) is not in the
   account profile '${AWS_PROFILE_FOR_ENV}' reaches, or is not readable there. The env file and the
   profile must describe the same estate — check both before wiring ${GH_ENV} to either."

# Prints the role's ARN, or dies saying why it cannot.
role_arn() {
    local name="$1" out
    if ! out="$(aws_q iam get-role --role-name "${name}" --query Role.Arn --output text 2>&1)"; then
        if [[ "${out}" == *NoSuchEntity* ]]; then
            die "the role ${name} does not exist: ${NOT_BOOTSTRAPPED}"
        fi
        die "could not read the role ${name}: $(tail -1 <<<"${out}")"
    fi
    printf '%s' "${out}"
}

# Every value a trust-policy condition carries for one claim, one per line. A
# condition value may be a string or a list; the JMESPath flatten handles both.
trust_values() {
    local name="$1" operator="$2" claim="$3"
    aws_q iam get-role --role-name "${name}" --output text \
        --query "Role.AssumeRolePolicyDocument.Statement[].Condition.${operator}.\"token.actions.githubusercontent.com:${claim}\" | []" |
        tr '\t' '\n' | sed '/^$/d; /^None$/d'
}

# Exactly one value, and it is the expected one. "Contains" would pass a policy
# that also trusts another environment or branch.
expect_trust() {
    local name="$1" operator="$2" claim="$3" want="$4" got
    got="$(trust_values "${name}" "${operator}" "${claim}")" ||
        die "could not read the trust policy of ${name}"
    if [[ "${got}" != "${want}" ]]; then
        die "${name} trusts ${claim} = '${got//$'\n'/, }', but ${GH_ENV} (mode ${MODE}) needs '${want}'.
   These are another environment's or repository's roles. Check the profile, --mode and --repo."
    fi
}

PLAN_ARN="$(role_arn "${TF_PLAN_ROLE_NAME}")"
APPLY_ARN="$(role_arn "${TF_APPLY_ROLE_NAME}")"
expect_trust "${TF_PLAN_ROLE_NAME}" StringEquals sub "${EXPECTED_SUB}"
expect_trust "${TF_APPLY_ROLE_NAME}" StringEquals sub "${EXPECTED_SUB}"
expect_trust "${TF_APPLY_ROLE_NAME}" StringEquals job_workflow_ref "${EXPECTED_APPLY_REF}"

# The FLIP root attaches this boundary to every role it owns; an apply against an
# account without it fails every role update with NoSuchEntity.
boundary_arn="arn:${PARTITION}:iam::${ACCOUNT}:policy/${TF_BOUNDARY_NAME}"
if ! out="$(aws_q iam get-policy --policy-arn "${boundary_arn}" --query Policy.PolicyName --output text 2>&1)"; then
    if [[ "${out}" == *NoSuchEntity* ]]; then
        die "the permissions boundary ${TF_BOUNDARY_NAME} does not exist: ${NOT_BOOTSTRAPPED}"
    fi
    die "could not read the permissions boundary ${TF_BOUNDARY_NAME}: $(tail -1 <<<"${out}")"
fi

echo "   state bucket, both roles, their trust policies and the boundary check out"

# ---------------------------------------------------------------------------
# 1. The environment itself
# ---------------------------------------------------------------------------
#
# aws-stag carries NO deployment branch policy on purpose: the plan job runs on
# pull requests, and a branch policy would stop it before it could mint a token.
# The apply role is not protected by this — it is pinned by IAM to
# terraform_apply.yml@refs/heads/develop (modules/terraform_ci_bootstrap).
#
# aws-prod restricts to `main`, and ONLY `main`. That is load-bearing security,
# not decoration: a GitHub environment's secrets are readable by any workflow that
# declares the environment and runs on an admitted branch, so admitting the default
# branch would let every workflow merged to develop read the production secrets —
# before making any AWS call, so the OIDC trust policies do not constrain it.
#
# The nightly drift job used to be the reason to admit develop (a `schedule` only
# ever fires from the default branch). It no longer is: the develop-scheduled run
# dispatches terraform_drift.yml onto `main` and the production leg runs there.
# See .github/workflows/terraform_drift.yml.
#
# The environment names are deliberately NOT part of the mode. Repointing an
# estate at another AWS account keeps aws-stag / aws-prod and changes their
# values: the branch policy above is a property of the *pipeline*, not of the
# account, and re-creating the environments to rename them would drop every
# secret they hold.
echo "🔧 ${GH_ENV} on ${REPO} (mode ${MODE})"
if ((DRY_RUN)); then
    echo "   [dry-run] create environment ${GH_ENV}"
    if [[ "${ENV}" == "prod" ]]; then
        echo "   [dry-run] branch policy: main only"
    else
        echo "   [dry-run] branch policy: none (PR plans must be able to run)"
    fi
else
    if [[ "${ENV}" == "prod" ]]; then
        gh api -X PUT "repos/${REPO}/environments/${GH_ENV}" \
            --input - >/dev/null <<'JSON'
{"deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}}
JSON
        # Reported from what the API actually accepted. The `|| true` this
        # replaces swallowed a rejection and then printed "branch policy: main"
        # over an environment with no policy at all — the failure mode being
        # guarded against, announced as the guard.
        policy_error="$(
            gh api -X POST "repos/${REPO}/environments/${GH_ENV}/deployment-branch-policies" \
                -f "name=main" -f "type=branch" 2>&1 >/dev/null
        )" || true
        applied="$(
            gh api "repos/${REPO}/environments/${GH_ENV}/deployment-branch-policies" \
                --jq '[.branch_policies[].name] | join(", ")' 2>/dev/null
        )" || applied=""
        if [[ "${applied}" == "main" ]]; then
            echo "   branch policy: main only"
        elif [[ -n "${applied}" ]]; then
            die "branch policy on ${GH_ENV} is '${applied}', not 'main' alone.
   Any branch listed here can read the production secrets. Remove the extras in
   Settings > Environments > ${GH_ENV} before using this environment."
        else
            die "could not confirm the branch policy on ${GH_ENV}: ${policy_error:-no policies returned}.
   Leaving it unset would let any branch read the production secrets."
        fi
    else
        gh api -X PUT "repos/${REPO}/environments/${GH_ENV}" \
            --input - >/dev/null <<'JSON'
{"deployment_branch_policy": null}
JSON
        echo "   branch policy: none (PR plans must be able to run)"
    fi
fi

# ---------------------------------------------------------------------------
# 2. The mode, then the role ARNs verified above
# ---------------------------------------------------------------------------

# Which mode the workflows should run in — the value terraform_plan.yml /
# terraform_apply.yml / terraform_drift.yml read as `vars.TF_PROD` and pass to
# compose-ci-env.sh as PROD. Set here rather than by hand so the environments this
# script populates and the mode it was invoked with cannot disagree; rollback is
# re-running this with the other mode's token.
set_gh variable TF_PROD "${MODE}"
echo "   set TF_PROD=${MODE}"

set_gh variable TF_PLAN_ROLE_ARN "${PLAN_ARN}"
set_gh variable TF_APPLY_ROLE_ARN "${APPLY_ARN}"
echo "   set TF_PLAN_ROLE_ARN, TF_APPLY_ROLE_ARN"

# ---------------------------------------------------------------------------
# 3. Everything the workflows dereference
# ---------------------------------------------------------------------------

# What is collected is the name on the GitHub side, not the environment-variable
# name the workflow binds it to. Usually they are the same, but not always:
# `FALLBACK_DOCKER_TAG: ${{ vars.DOCKER_TAG }}` feeds the image-tag resolver, and
# the object that has to exist in the GitHub environment is `DOCKER_TAG`. Reading
# the left-hand side would invent a `FALLBACK_DOCKER_TAG` variable that nothing
# reads, while leaving the one that is actually dereferenced unchecked.
#
# Still anchored to a `KEY: ${{ … }}` line inside an `env:` block, which is what
# keeps `role-to-assume: ${{ vars.TF_PLAN_ROLE_ARN }}` out of the list — that one
# is read from IAM above, not from the operator's env file.
mapfile -t SECRET_KEYS < <(
    grep -oE '^[[:space:]]+[A-Z][A-Z0-9_]*:[[:space:]]+\$\{\{[[:space:]]*secrets\.[A-Z0-9_]+' "${PLAN_WORKFLOW}" |
        sed -E 's/.*secrets\.//' | sort -u
)
mapfile -t VARIABLE_KEYS < <(
    grep -oE '^[[:space:]]+[A-Z][A-Z0-9_]*:[[:space:]]+\$\{\{[[:space:]]*vars\.[A-Z0-9_]+' "${PLAN_WORKFLOW}" |
        sed -E 's/.*vars\.//' | sort -u
)
((${#SECRET_KEYS[@]} > 0)) || die "found no 'secrets.' references in ${PLAN_WORKFLOW} — has its env block changed shape?"

set_one() {
    local kind="$1" key="$2" value="${VALUES[$2]:-}"
    if [[ -z "${value}" ]]; then
        # ENFORCE_MFA is legitimately empty in production — locals.tf omits it
        # from the task env so flip-api's secure default applies.
        [[ "${key}" == "ENFORCE_MFA" ]] && { echo "   skip ${key} (intentionally empty)"; return 0; }
        # DEMO_ASSETS_BUCKET_NAME is OPTIONAL in the manifest because empty is
        # correct on stag, which hosts no public Ark+ demo. On prod empty is not
        # a value but a gap: cloudfront.tf gates the demo's bucket policy,
        # public-access block, OAC and /ark_demo/* behaviour on it being
        # non-empty, so seeding prod without it destroys all four on the next
        # apply. Same asymmetry as keys_expected_empty() in reconcile_ci_env.py.
        if [[ "${key}" == "DEMO_ASSETS_BUCKET_NAME" && "${ENV}" == "prod" ]]; then
            MISSING+=("${key}")
            return 0
        fi
        if [[ " ${REQUIRED_KEYS[*]} " == *" ${key} "* ]]; then
            MISSING+=("${key}")
        else
            OPTIONAL_ABSENT+=("${key}")
        fi
        return 0
    fi
    set_gh "${kind}" "${key}" "${value}"
    SET+=("${key}")
}

# Which keys are load-bearing comes from compose-ci-env.sh, the thing that
# actually rejects a missing one — so this warns about what will break the run,
# not about every key the local file happens not to carry. That script holds two
# lists, not one: the keys every mode needs, and the LZA keys that only the two
# platform-managed modes require (the self-contained ones legitimately leave them
# out). Both are read here, the second only when this mode is an LZA one.
mapfile -t REQUIRED_KEYS < <(
    sed -n '/^REQUIRED_KEYS=(/,/^)/p' "${HERE}/compose-ci-env.sh" |
        grep -oE '^[[:space:]]+[A-Z][A-Z0-9_]*$' | sed -E 's/^[[:space:]]+//'
)
((${#REQUIRED_KEYS[@]} > 0)) || die "could not read REQUIRED_KEYS from compose-ci-env.sh"
if ((IS_LZA)); then
    mapfile -t LZA_KEYS < <(
        sed -n '/^LZA_REQUIRED_KEYS=(/,/^)/p' "${HERE}/compose-ci-env.sh" |
            grep -oE '^[[:space:]]+[A-Z][A-Z0-9_]*$' | sed -E 's/^[[:space:]]+//'
    )
    ((${#LZA_KEYS[@]} > 0)) || die "could not read LZA_REQUIRED_KEYS from compose-ci-env.sh"
    REQUIRED_KEYS+=("${LZA_KEYS[@]}")
fi

SET=()
MISSING=()
OPTIONAL_ABSENT=()
for key in "${SECRET_KEYS[@]}"; do set_one secret "${key}"; done
for key in "${VARIABLE_KEYS[@]}"; do set_one variable "${key}"; done

echo ""
echo "   ${#SECRET_KEYS[@]} secret(s) + ${#VARIABLE_KEYS[@]} variable(s) referenced by the workflows"
echo "   ${#SET[@]} set from ${ENV_FILE}"
if ((${#OPTIONAL_ABSENT[@]} > 0)); then
    echo "   ${#OPTIONAL_ABSENT[@]} optional key(s) absent, which is fine: ${OPTIONAL_ABSENT[*]}"
fi
if ((${#MISSING[@]} > 0)); then
    echo ""
    echo "   ⚠️  REQUIRED but absent from ${ENV_FILE} — the workflow will fail without these:"
    printf '      - %s\n' "${MISSING[@]}"
    echo ""
    echo "      scripts/reconcile_ci_env.py --env ${ENV} --profile ${AWS_PROFILE_FOR_ENV} --compare ${ENV_FILE}"
    echo "      recovers most of them from the deployed infrastructure."
fi
# Not `((DRY_RUN)) && echo …` as the last statement: on a real run `((0))` is
# false, that becomes the script's exit status, and every non-dry-run exited 1
# while having done its job perfectly.
if ((DRY_RUN)); then
    echo ""
    echo "   (dry run — nothing was written)"
fi
