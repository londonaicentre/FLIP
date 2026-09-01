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
# Nothing is printed except key names — values go straight from the local env
# file into GitHub.
#
# The secret-vs-variable split is not hard-coded here. It is read out of
# .github/workflows/terraform_plan.yml, because that workflow is what actually
# dereferences them: a key the workflow reads as `secrets.X` but that was stored
# as a variable resolves to empty, and compose-ci-env.sh then fails the run with
# a missing-key error that points at the wrong thing. Deriving the split removes
# the chance of that disagreeing.
#
# Idempotent: re-running updates values in place.
#
# Usage:
#     scripts/setup-github-environments.sh --env stag --env-file ../../../.env.stag
#     scripts/setup-github-environments.sh --env prod --env-file ../../../.env.production --dry-run

set -euo pipefail

die() {
    echo "❌ $*" >&2
    exit 1
}

REPO="${REPO:-londonaicentre/FLIP}"
TF_ENV=""
ENV_FILE=""
DRY_RUN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --env) TF_ENV="$2"; shift 2 ;;
        --env-file) ENV_FILE="$2"; shift 2 ;;
        --repo) REPO="$2"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        *) die "unknown argument: $1" ;;
    esac
done

[[ -n "${TF_ENV}" ]] || die "usage: $0 --env stag|prod --env-file <path> [--dry-run]"
case "${TF_ENV}" in stag | prod) ;; *) die "--env must be 'stag' or 'prod'" ;; esac
[[ -n "${ENV_FILE}" && -f "${ENV_FILE}" ]] || die "--env-file must point at an existing file (got '${ENV_FILE}')"
command -v gh >/dev/null || die "gh CLI is required"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AWS_DIR="$(cd "${HERE}/.." && pwd)"
REPO_ROOT="$(cd "${AWS_DIR}/../../.." && pwd)"
PLAN_WORKFLOW="${REPO_ROOT}/.github/workflows/terraform_plan.yml"
[[ -f "${PLAN_WORKFLOW}" ]] || die "cannot find ${PLAN_WORKFLOW} — the secret/variable split is read from it"

GH_ENV="aws-${TF_ENV}"
# Matches the Makefile's PROD_AWS_PROFILE / STAG_AWS_PROFILE convention; override
# if your local profile names differ.
AWS_PROFILE_FOR_ENV="${AWS_PROFILE_FOR_ENV:-${TF_ENV}}"

run() {
    if ((DRY_RUN)); then
        echo "   [dry-run] $*"
    else
        "$@" >/dev/null
    fi
}

# ---------------------------------------------------------------------------
# 1. The environment itself
# ---------------------------------------------------------------------------
#
# aws-stag carries NO deployment branch policy on purpose: the plan job runs on
# pull requests, and a branch policy would stop it before it could mint a token.
# The apply role is not protected by this — it is pinned by IAM to
# terraform_apply.yml@refs/heads/develop (see ci/main.tf).
#
# aws-prod restricts to `main` (applies) plus the default branch, because
# scheduled workflows always run on the default branch and the nightly drift job
# needs to reach the read-only plan role. That is load-bearing security, not
# decoration: without it a PR could name aws-prod and read production secrets.
echo "🔧 ${GH_ENV} on ${REPO}"
if ((DRY_RUN)); then
    echo "   [dry-run] create environment ${GH_ENV}"
else
    if [[ "${TF_ENV}" == "prod" ]]; then
        gh api -X PUT "repos/${REPO}/environments/${GH_ENV}" \
            --input - >/dev/null <<'JSON'
{"deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}}
JSON
        default_branch="$(gh api "repos/${REPO}" --jq .default_branch)"
        for branch in main "${default_branch}"; do
            gh api -X POST "repos/${REPO}/environments/${GH_ENV}/deployment-branch-policies" \
                -f "name=${branch}" -f "type=branch" >/dev/null 2>&1 || true
        done
        echo "   branch policy: main + ${default_branch} (drift runs on the default branch)"
    else
        gh api -X PUT "repos/${REPO}/environments/${GH_ENV}" \
            --input - >/dev/null <<'JSON'
{"deployment_branch_policy": null}
JSON
        echo "   branch policy: none (PR plans must be able to run)"
    fi
fi

# ---------------------------------------------------------------------------
# 2. Role ARNs, straight from the CI Terraform root
# ---------------------------------------------------------------------------

# terraform needs to be told which account to talk to, and told to ignore any
# static AWS_* left in the shell — same reasoning as the Makefile's `_TF`, where
# a stale AWS_ACCESS_KEY_ID outranks the SSO chain and yields InvalidClientTokenId.
# Without AWS_PROFILE this silently reads the default profile and fails with an
# opaque InvalidGrantException.
ci_output() {
    (
        cd "${AWS_DIR}/ci" || exit 1
        unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_SECURITY_TOKEN
        AWS_PROFILE="${AWS_PROFILE_FOR_ENV}" terraform output -raw "$1" 2>/dev/null
    ) || true
}

# ci/ is a single working directory re-pointed between accounts by `make -C ci
# init [PROD=true]`. If it is currently initialised for the *other* environment,
# `terraform output` happily returns that account's ARNs — and writing those into
# this environment would point stag's workflows at prod's roles, or the reverse.
# The backend bucket records which account the working directory is bound to.
ci_backend_bucket="$(
    grep -o '"bucket": *"[^"]*"' "${AWS_DIR}/ci/.terraform/terraform.tfstate" 2>/dev/null |
        head -1 | sed -E 's/.*"bucket": *"([^"]*)".*/\1/'
)"
expected_bucket="flip-terraform-state-${TF_ENV}"
if [[ -n "${ci_backend_bucket}" && "${ci_backend_bucket}" != "${expected_bucket}" ]]; then
    die "ci/ is initialised for '${ci_backend_bucket}', not '${expected_bucket}'.
   Reading role ARNs now would wire ${GH_ENV} to the wrong account's roles. Run:
       make -C ci init$([[ "${TF_ENV}" == prod ]] && echo ' PROD=true')"
fi

plan_arn="$(ci_output plan_role_arn)"
apply_arn="$(ci_output apply_role_arn)"
if [[ -z "${plan_arn}" || -z "${apply_arn}" ]]; then
    echo "   ⚠️  Could not read the role ARNs from ci/ state — is it initialised for ${TF_ENV}?"
    echo "      Run: make -C ci init$([[ "${TF_ENV}" == prod ]] && echo ' PROD=true') && make -C ci output"
    echo "      Then set TF_PLAN_ROLE_ARN / TF_APPLY_ROLE_ARN by hand."
else
    run gh variable set TF_PLAN_ROLE_ARN --env "${GH_ENV}" --repo "${REPO}" --body "${plan_arn}"
    run gh variable set TF_APPLY_ROLE_ARN --env "${GH_ENV}" --repo "${REPO}" --body "${apply_arn}"
    echo "   set TF_PLAN_ROLE_ARN, TF_APPLY_ROLE_ARN"
fi

# ---------------------------------------------------------------------------
# 3. Everything the workflows dereference
# ---------------------------------------------------------------------------

mapfile -t SECRET_KEYS < <(
    grep -oE '^[[:space:]]+[A-Z][A-Z0-9_]*:[[:space:]]+\$\{\{[[:space:]]*secrets\.' "${PLAN_WORKFLOW}" |
        sed -E 's/^[[:space:]]+([A-Z0-9_]+):.*/\1/' | sort -u
)
mapfile -t VARIABLE_KEYS < <(
    grep -oE '^[[:space:]]+[A-Z][A-Z0-9_]*:[[:space:]]+\$\{\{[[:space:]]*vars\.' "${PLAN_WORKFLOW}" |
        sed -E 's/^[[:space:]]+([A-Z0-9_]+):.*/\1/' | sort -u
)
((${#SECRET_KEYS[@]} > 0)) || die "found no 'secrets.' references in ${PLAN_WORKFLOW} — has its env block changed shape?"

# Read the env file the way make does: last assignment wins, value verbatim.
declare -A VALUES=()
while IFS= read -r line; do
    [[ "${line}" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]] && VALUES["${BASH_REMATCH[1]}"]="${BASH_REMATCH[2]}"
done <"${ENV_FILE}"

set_one() {
    local kind="$1" key="$2" value="${VALUES[$2]:-}"
    if [[ -z "${value}" ]]; then
        # ENFORCE_MFA is legitimately empty in production — locals.tf omits it
        # from the task env so flip-api's secure default applies.
        [[ "${key}" == "ENFORCE_MFA" ]] && { echo "   skip ${key} (intentionally empty)"; return 0; }
        if [[ " ${REQUIRED_KEYS[*]} " == *" ${key} "* ]]; then
            MISSING+=("${key}")
        else
            OPTIONAL_ABSENT+=("${key}")
        fi
        return 0
    fi
    if [[ "${kind}" == "secret" ]]; then
        run gh secret set "${key}" --env "${GH_ENV}" --repo "${REPO}" --body "${value}"
    else
        run gh variable set "${key}" --env "${GH_ENV}" --repo "${REPO}" --body "${value}"
    fi
    SET+=("${key}")
}

# Which keys are load-bearing comes from compose-ci-env.sh, the thing that
# actually rejects a missing one — so this warns about what will break the run,
# not about every key the local file happens not to carry.
mapfile -t REQUIRED_KEYS < <(
    sed -n '/^REQUIRED_KEYS=(/,/^)/p' "${HERE}/compose-ci-env.sh" |
        grep -oE '^[[:space:]]+[A-Z][A-Z0-9_]*$' | sed -E 's/^[[:space:]]+//'
)
((${#REQUIRED_KEYS[@]} > 0)) || die "could not read REQUIRED_KEYS from compose-ci-env.sh"

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
    echo "      scripts/reconcile_ci_env.py --env ${TF_ENV} --profile ${TF_ENV} --compare ${ENV_FILE}"
    echo "      recovers most of them from the deployed infrastructure."
fi
((DRY_RUN)) && echo "" && echo "   (dry run — nothing was written)"
