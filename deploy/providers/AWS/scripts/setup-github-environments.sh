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

# ---------------------------------------------------------------------------
# 1. The environment itself
# ---------------------------------------------------------------------------
#
# aws-stag carries NO deployment branch policy on purpose: the plan job runs on
# pull requests, and a branch policy would stop it before it could mint a token.
# The apply role is not protected by this — it is pinned by IAM to
# terraform_apply.yml@refs/heads/develop (see ci/main.tf).
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
echo "🔧 ${GH_ENV} on ${REPO}"
if ((DRY_RUN)); then
    echo "   [dry-run] create environment ${GH_ENV}"
    if [[ "${TF_ENV}" == "prod" ]]; then
        echo "   [dry-run] branch policy: main only"
    else
        echo "   [dry-run] branch policy: none (PR plans must be able to run)"
    fi
else
    if [[ "${TF_ENV}" == "prod" ]]; then
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
# The state bucket name is the same in both FLIP accounts today, and the LZA
# cutover (#749) keeps the naming — but it is a default, not an assumption:
# override CI_STATE_BUCKET if an account ever uses another name.
CI_STATE_BUCKET="${CI_STATE_BUCKET:-flip-terraform-state-${TF_ENV}}"
CI_BACKEND_STATE="${AWS_DIR}/ci/.terraform/terraform.tfstate"

# A fresh checkout has no ci/.terraform at all. The grep below then exits 2, and
# under `set -o pipefail` that killed the whole script — silently, because grep's
# stderr is discarded and nothing had been printed yet. Checked explicitly so the
# operator is told which command to run.
[[ -f "${CI_BACKEND_STATE}" ]] || die "ci/ has not been initialised in this checkout (${CI_BACKEND_STATE} is missing).
   The role ARNs are read from its state. Run:
       make -C ci init$([[ "${TF_ENV}" == prod ]] && echo ' PROD=true')"

ci_backend_bucket="$(
    grep -o '"bucket": *"[^"]*"' "${CI_BACKEND_STATE}" |
        head -1 | sed -E 's/.*"bucket": *"([^"]*)".*/\1/'
)" || ci_backend_bucket=""
if [[ -n "${ci_backend_bucket}" && "${ci_backend_bucket}" != "${CI_STATE_BUCKET}" ]]; then
    die "ci/ is initialised for '${ci_backend_bucket}', not '${CI_STATE_BUCKET}'.
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
    set_gh variable TF_PLAN_ROLE_ARN "${plan_arn}"
    set_gh variable TF_APPLY_ROLE_ARN "${apply_arn}"
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
        # DEMO_ASSETS_BUCKET_NAME is OPTIONAL in the manifest because empty is
        # correct on stag, which hosts no public Ark+ demo. On prod empty is not
        # a value but a gap: cloudfront.tf gates the demo's bucket policy,
        # public-access block, OAC and /ark_demo/* behaviour on it being
        # non-empty, so seeding prod without it destroys all four on the next
        # apply. Same asymmetry as keys_expected_empty() in reconcile_ci_env.py.
        if [[ "${key}" == "DEMO_ASSETS_BUCKET_NAME" && "${TF_ENV}" == "prod" ]]; then
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
# Not `((DRY_RUN)) && echo …` as the last statement: on a real run `((0))` is
# false, that becomes the script's exit status, and every non-dry-run exited 1
# while having done its job perfectly.
if ((DRY_RUN)); then
    echo ""
    echo "   (dry run — nothing was written)"
fi
