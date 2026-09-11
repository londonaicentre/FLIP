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

# Compose the Terraform env file (.env.stag / .env.production) that
# deploy/providers/AWS/Makefile `include`s, from values already present in the
# process environment.
#
# In CI those values arrive as GitHub *environment* secrets and variables
# (environments `aws-stag` / `aws-prod`), injected by the workflow's `env:` block.
# Nothing here is AWS- or GitHub-specific: the script only reads the environment,
# so it is equally runnable from a laptop to check a candidate value set.
#
# Why compose a file at all, rather than exporting TF_VAR_* directly: the Makefile
# is the single source of truth for how env values map onto Terraform inputs (it
# derives TF_VAR_environment, the subnet CIDRs, the FL image names via
# deploy/fl_backend.mk, and the kit-date selection). CI feeds that machinery its
# normal input instead of re-deriving the mapping and drifting from what a laptop
# `make plan` would produce.
#
# The failure mode this design invites is a *silently missing* key: the Makefile
# would export TF_VAR_x="" and Terraform would plan a destructive diff from an
# empty string. So every required key is checked here and the script exits
# non-zero listing all of them at once.
#
# Values are never echoed — errors name keys only, so a secret cannot reach the
# workflow log through this script.
#
# Usage:
#     TF_ENV=stag|prod scripts/compose-ci-env.sh <output-file>

set -euo pipefail

die() {
    echo "❌ $*" >&2
    exit 1
}

OUT_FILE="${1:-}"
[[ -n "${OUT_FILE}" ]] || die "usage: TF_ENV=stag|prod $0 <output-file>"

TF_ENV="${TF_ENV:-stag}"
case "${TF_ENV}" in
    stag | prod) ;;
    *) die "TF_ENV must be 'stag' or 'prod' (got '${TF_ENV}')" ;;
esac

# Keys required for every `make init` / `make plan` / `make apply`.
#
# Derived from deploy/providers/AWS/Makefile: every `${VAR}` interpolated into an
# unconditional `export TF_VAR_…` line, plus the values the Makefile's own
# fail-fast guards and the `init` backend config need.
# scripts/tests/test_compose_ci_env.sh cross-checks this list against the Makefile
# and fails when the two drift, so adding a TF_VAR export without adding the key
# here (and to both GitHub environments) is caught in CI rather than at apply time.
REQUIRED_KEYS=(
    # Backend + provider wiring (Makefile `init`, TF_VAR_AWS_REGION)
    AWS_REGION
    FLIP_TFSTATE_BUCKET_NAME
    VPC_NAME

    # Buckets — all four carry Makefile placeholder guards
    AICENTRE_BUCKET_NAME
    FLIP_APP_BUNDLES_BUCKET_NAME
    FLIP_FL_RESULTS_BUCKET_NAME
    FLIP_MODEL_FILES_UPLOADS_BUCKET_NAME
    FLIP_UI_BUCKET_NAME

    # Secrets — stored as GitHub environment secrets
    ADMIN_USER_PASSWORD
    AES_KEY_BASE64
    INTERNAL_SERVICE_KEY
    INTERNAL_SERVICE_KEY_HASH

    # Database identifiers — configuration, stored as GitHub environment
    # *variables*. They are rendered in the clear into the public plan comment
    # either way (../variables.tf explains the decision), and production does not
    # authenticate with them: RDS Proxy mints a per-connection IAM token.
    POSTGRES_DB
    POSTGRES_USER

    # Service wiring baked into the ECS task definitions (locals.tf)
    API_PORT
    DB_PORT
    FL_ADMIN_DIRECTORY
    FL_API_PORT
    FL_SERVER_PORT
    INTERNAL_SERVICE_KEY_HEADER
    MIN_CLIENTS
    SES_VERIFIED_EMAIL
    TRUST_API_KEY_HEADER
    # UI_PORT is referenced by no resource in this root (the UI is served from S3
    # via CloudFront, not a port), so it looks omittable — but the Makefile exports
    # it unconditionally, which turns an absent key into TF_VAR_UI_PORT="" and
    # Terraform rejects that for a `number` variable:
    #     Unsuitable value for var.UI_PORT … a number is required
    # Verified against a real prod plan. The same reasoning keeps every other
    # numeric key required; only JOB_RESOURCE_SPEC_* may be absent, because the
    # Makefile guards those exports behind `ifneq`.
    UI_PORT

    # DNS
    ALB_SUBDOMAIN
    NLB_SUBDOMAIN

    # Images
    DOCKER_REGISTRY
    DOCKER_TAG
    DOCKER_FL_TAG

    # FL
    FL_BACKEND
    FL_KIT_SLOT_NAMES

    # The three keys whose Makefile default is DESTRUCTIVE rather than inert.
    #
    #   DEPLOY_TRUST_EC2       ?= true  — absent means "create a t3.xlarge cloud
    #                                     trust host", which staging does not run.
    #   LOCAL_TRUST_PUBLIC_IPS ?= []    — absent means "no on-prem trust may reach
    #   K8S_TRUST_PUBLIC_IPS   ?= []      the FL-server NLB", i.e. delete every
    #                                     ingress rule those trusts connect through.
    #
    # None of the three is on check-fl-plan-impact.sh's watch list, so an
    # unattended apply would make all of it silently. They are required here with
    # an explicit literal — `false` / `[]` written down is a decision; an absent
    # key is an accident that reads identically to Terraform.
    DEPLOY_TRUST_EC2
    K8S_TRUST_PUBLIC_IPS
    LOCAL_TRUST_PUBLIC_IPS
)

# Keys the Makefile exports only when set, or supplies a `?=` default for. Passed
# through when present; their absence is not an error, because the Makefile or
# variables.tf already has a defined fallback.
OPTIONAL_KEYS=(
    FLIP_BUCKET_NAME
    JOB_RESOURCE_SPEC_MEM_PER_GPU_IN_GIB
    JOB_RESOURCE_SPEC_NUM_GPUS

    # Empty is meaningful and NOT symmetric with the rest of this list: it is the
    # correct value on stag, which hosts no public Ark+ demo, and a destructive
    # one on prod, where `demo_assets_enabled = var.DEMO_ASSETS_BUCKET_NAME != ""`
    # (cloudfront.tf) gates four live resources plus the /ark_demo/* behaviour.
    # Optional here so stag composes, recovered from state by reconcile_ci_env.py
    # so prod's GitHub environment cannot be seeded without it.
    DEMO_ASSETS_BUCKET_NAME

    # ENFORCE_MFA is optional because *empty is the intended production value*,
    # not an oversight: locals.tf omits the variable from the flip-api task env
    # when it is "", so flip-api's Pydantic default (True) applies — the secure
    # anchor. Requiring a non-empty value here would reject the correct prod
    # configuration. Only stag sets it (to "false", for testing).
    ENFORCE_MFA

    # The LZA keys (FLIP#749). CI drives only the two self-contained accounts —
    # `TF_ENV` admits stag and prod alone, and a platform-managed estate is
    # applied from a laptop with PROD=lza / PROD=lza-stag — so all five are
    # expected to be *absent* from the aws-stag / aws-prod GitHub environments,
    # and absent is the legacy value in every case:
    #
    #   ACCESS_LOGS_BUCKET_NAME  — "" derives flip-access-logs-<flip_alb_subdomain>
    #   CF_LOGS_BUCKET_NAME      — "" derives flip-cf-logs-<flip_alb_subdomain>
    #   EFS_PROVISION_IMAGE      — Makefile exports it only when set; variables.tf
    #                              keeps the Docker Hub amazon/aws-cli default
    #   LZA_VPC_NAME             — same `ifneq` guard; names the accelerator VPC
    #   MANAGE_DNS               — Makefile `?= true`, i.e. the DNS-managed shape
    #
    # They are still carried through the workflows and listed here rather than
    # exempted, so the drift guard above keeps covering them if CI ever gains an
    # LZA environment.
    ACCESS_LOGS_BUCKET_NAME
    CF_LOGS_BUCKET_NAME
    EFS_PROVISION_IMAGE
    LZA_VPC_NAME
    MANAGE_DNS

)
# Deliberately absent: PRESERVE_VPC. It is a make-level flag read only by
# `make destroy` (scripts/destroy-selective.sh), never a Terraform input — and CI
# has no destroy path, by design.

# The kit date for the *selected* backend is required (the Makefile hard-errors
# without it — "FLARE_KIT_DATE must be set … for FL_BACKEND=…"). The other
# backend's date is optional: variables.tf defaults both to "".
case "${FL_BACKEND:-}" in
    nvflare)
        REQUIRED_KEYS+=(FLARE_KIT_DATE)
        OPTIONAL_KEYS+=(FLOWER_KIT_DATE)
        ;;
    flower)
        REQUIRED_KEYS+=(FLOWER_KIT_DATE)
        OPTIONAL_KEYS+=(FLARE_KIT_DATE)
        ;;
    "")
        die "FL_BACKEND is not set — it selects which kit date is required. Set it in the ${TF_ENV} GitHub environment."
        ;;
    *)
        die "FL_BACKEND must be 'nvflare' or 'flower' (got '${FL_BACKEND}')"
        ;;
esac

# AWS_PROFILE is *not* a stored value, and in CI it does not name a profile at
# all. It exists solely to satisfy the Makefile's account guard, which refuses to
# parse unless AWS_PROFILE equals PROD_AWS_PROFILE / STAG_AWS_PROFILE — a guard
# written for laptops, where the profile really is how you choose an account.
#
# On a runner the account comes from the OIDC role that
# aws-actions/configure-aws-credentials has already assumed into AWS_ACCESS_KEY_ID
# and friends; no ~/.aws/config is written by any workflow, and nothing would read
# one if it were. The value never leaves the Makefile either: `make print-tf-env`
# emits only `TF_VAR_*` lines, so AWS_PROFILE does not reach the terraform steps.
#
# Deriving it from TF_ENV rather than storing it is still what stops a mis-set
# GitHub variable from pointing a stag run's Makefile at the prod branch of that
# guard — which is the only decision the value drives.
AWS_PROFILE_VALUE="${TF_ENV}"

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

missing=()
placeholder=()
malformed=()

# A copied-but-unedited env file leaves `<your-bucket-name>` in place. Those are
# non-empty, so an emptiness check passes them through to Terraform, which fails
# much later with an opaque AWS error. Mirrors `unset_or_placeholder` in the Makefile.
is_placeholder() {
    [[ "$1" == "<"*">" ]]
}

# Make parses an included file line-by-line *before* expansion, so three classes of
# value need handling:
#
#   `#` and `$`  — a raw `#` starts a comment and `$` starts a variable reference.
#                  Both have Make escapes (`\#`, `$$`) and are escaped on write below,
#                  so a generated password containing either survives.
#   `\`           — has no general escape. Make special-cases it only before `#` and
#                  before a newline, where it continues the line. That makes a value
#                  ending in a backslash swallow the next key entirely, and a value
#                  containing `\#` unescapable (escaping the `#` yields `\\#`, an even
#                  number of backslashes, which Make reads as an unescaped comment).
#                  Rejected rather than half-handled.
#   newline / trailing whitespace
#                  — a newline cannot be carried at all, and Make silently retains
#                  trailing whitespace (it strips leading), a classic source of values
#                  that look right and are not.
is_malformed() {
    local value="$1"
    [[ "${value}" == *$'\n'* || "${value}" == *$'\r'* ]] && return 0
    [[ "${value}" == *\\* ]] && return 0
    [[ "${value}" =~ [[:space:]]$ ]] && return 0
    return 1
}

for key in "${REQUIRED_KEYS[@]}"; do
    if [[ -z "${!key:-}" ]]; then
        missing+=("${key}")
    elif is_placeholder "${!key}"; then
        placeholder+=("${key}")
    elif is_malformed "${!key}"; then
        malformed+=("${key}")
    fi
done

for key in "${OPTIONAL_KEYS[@]}"; do
    if [[ -n "${!key:-}" ]] && is_malformed "${!key}"; then
        malformed+=("${key}")
    fi
done

if ((${#missing[@]} > 0)) || ((${#placeholder[@]} > 0)) || ((${#malformed[@]} > 0)); then
    echo "❌ Cannot compose ${OUT_FILE} for the '${TF_ENV}' environment." >&2
    ((${#missing[@]} > 0)) && {
        echo "" >&2
        echo "   Missing or empty (${#missing[@]}):" >&2
        printf '     - %s\n' "${missing[@]}" >&2
    }
    ((${#placeholder[@]} > 0)) && {
        echo "" >&2
        echo "   Still an unedited <placeholder> (${#placeholder[@]}):" >&2
        printf '     - %s\n' "${placeholder[@]}" >&2
    }
    ((${#malformed[@]} > 0)) && {
        echo "" >&2
        echo "   Contains a newline, a backslash, or trailing whitespace, which Make cannot carry (${#malformed[@]}):" >&2
        printf '     - %s\n' "${malformed[@]}" >&2
    }
    cat >&2 <<EOF

   Each name above must exist as a secret or variable on the GitHub environment
   'aws-${TF_ENV}', with the same value as the matching key in the operator's
   .env.${TF_ENV/prod/production} file. See deploy/providers/AWS/README.md,
   "Terraform CI: plan on PR, apply on merge" > "Where the values come from".
EOF
    exit 1
fi

# ---------------------------------------------------------------------------
# Emit
# ---------------------------------------------------------------------------

# `\#` and `$$` are Make's escapes for a literal `#` and `$` in a value. Applied
# on write so a generated password containing either survives the round trip.
escape_for_make() {
    local value="$1"
    value="${value//\$/\$\$}"
    value="${value//\#/\\#}"
    printf '%s' "${value}"
}

emit_to="$(mktemp)"
trap 'rm -f "${emit_to}"' EXIT

{
    echo "# Generated by deploy/providers/AWS/scripts/compose-ci-env.sh — do not edit."
    echo "# Environment: ${TF_ENV}. Values come from the GitHub environment 'aws-${TF_ENV}'."
    echo "AWS_PROFILE=${AWS_PROFILE_VALUE}"
} >"${emit_to}"

written=0
for key in "${REQUIRED_KEYS[@]}" "${OPTIONAL_KEYS[@]}"; do
    value="${!key:-}"
    [[ -n "${value}" ]] || continue
    printf '%s=%s\n' "${key}" "$(escape_for_make "${value}")" >>"${emit_to}"
    written=$((written + 1))
done

# Written with 0600 from the start: the file carries AES_KEY_BASE64 and the DB
# credentials, and a runner's workspace is world-readable by default.
install -m 600 "${emit_to}" "${OUT_FILE}"

echo "✅ Composed ${OUT_FILE} for '${TF_ENV}' — ${written} keys (${#REQUIRED_KEYS[@]} required, plus AWS_PROFILE)."
