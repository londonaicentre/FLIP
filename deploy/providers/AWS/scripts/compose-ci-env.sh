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
#     PROD=stag|true|lza-stag|lza scripts/compose-ci-env.sh [output-file]
#     PROD=stag|true|lza-stag|lza scripts/compose-ci-env.sh --print-env
#
# PROD is the deploy/env_mode.mk token — the same value the GitHub environment
# carries as the TF_PROD variable. It is the only mode input: from it this script
# derives which env file the Makefile will `include`, which AWS_PROFILE the
# Makefile's account guard expects, and which keys are required. One token, one
# table, no second place for the two to disagree.
#
# EXPECTED_ENV_CLASS is the caller's own statement of which *class* this run should
# be composing, and the workflows pass it from the ref (main -> prod, everything
# else -> stag; `stag` in terraform_plan.yml, which only ever plans staging). It is
# required when this runs in CI and ignored on a laptop. One freely settable
# variable now chooses the account shape, and the dangerous direction is silent:
# TF_PROD=stag on aws-prod composes a prod-sized plan whose RDS is refreshed as
# `deletion_protection = false`, `skip_final_snapshot = true` — in an unattended
# apply on main. The class is not a second token to keep in sync: it is the value
# env_mode.mk derives as ENV_CLASS, carried per row of the table below and pinned
# against that file by tests/test_ci_env_target.py, so a mis-set TF_PROD is a red
# compose step instead of a plan someone has to read closely.
#
# The output file defaults to <repo root>/.env.<ENV> — the exact path
# `deploy/providers/AWS/Makefile` derives through env_mode.mk — so CI cannot
# compose one file and have make include another. Pass a path to override (the
# test harness does).

set -euo pipefail

die() {
    echo "❌ $*" >&2
    exit 1
}

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# The env file has to land exactly where `deploy/providers/AWS/Makefile` derives it
# — MAIN_ENV_FILE = ../../../$(ENV_FILE_NAME), i.e. <repo root>/.env.<env>. That
# include is behind a wildcard guard, so a file written one directory off is
# skipped *silently*: Terraform then sees an empty input set, and the run fails
# later, in `make`, complaining about a missing key rather than a missing file.
# Resolved through git where possible (correct for a worktree, or a script invoked
# by absolute path) and then *checked* rather than assumed: an unrecognised layout
# stops the run instead of writing somewhere nothing will read.
REPO_ROOT="$(git -C "${HERE}" rev-parse --show-toplevel 2>/dev/null || true)"
[[ -n "${REPO_ROOT}" ]] || REPO_ROOT="$(cd "${HERE}/../../../.." && pwd)"
if [[ ! -f "${REPO_ROOT}/deploy/providers/AWS/Makefile" ]]; then
    die "cannot resolve the repository root from ${HERE} (tried '${REPO_ROOT}').
   The env file must be written where deploy/providers/AWS/Makefile includes it."
fi

# The mode table. ENV_FILE is the name `deploy/env_mode.mk` derives from the same
# token (ENV_FILE_NAME); AWS_PROFILE_VALUE is what the Makefile's account guard
# demands for that ENV (PROD_AWS_PROFILE / STAG_AWS_PROFILE / LZA_AWS_PROFILE /
# LZA_STAG_AWS_PROFILE); GH_ENV is the GitHub environment holding this
# environment's values; ENV_CLASS_VALUE is env_mode.mk's ENV_CLASS (prod | stag).
# The LZA tokens reuse aws-stag / aws-prod: the estate was repointed at the LZA
# accounts rather than given a third and fourth environment (README, "Repointing
# CI at the LZA accounts"). tests/test_ci_env_target.py pins ENV, ENV_FILE and
# ENV_CLASS against a real `make` probe, so this table cannot drift from
# env_mode.mk silently.
PROD_TOKEN="${PROD:-}"
case "${PROD_TOKEN}" in
    stag)
        ENV=stag
        ENV_FILE=.env.stag
        AWS_PROFILE_VALUE=stag
        GH_ENV=aws-stag
        ENV_CLASS_VALUE=stag
        ;;
    true)
        ENV=production
        ENV_FILE=.env.production
        AWS_PROFILE_VALUE=prod
        GH_ENV=aws-prod
        ENV_CLASS_VALUE=prod
        ;;
    lza-stag)
        ENV=lza-stag
        ENV_FILE=.env.lza-stag
        AWS_PROFILE_VALUE=lza-stag
        GH_ENV=aws-stag
        ENV_CLASS_VALUE=stag
        ;;
    lza)
        ENV=lza-prod
        ENV_FILE=.env.lza-prod
        AWS_PROFILE_VALUE=lza-prod
        GH_ENV=aws-prod
        ENV_CLASS_VALUE=prod
        ;;
    "")
        die "PROD is not set. It selects the mode: stag, true, lza-stag or lza.
   In CI it arrives from the TF_PROD variable on the GitHub environment
   (${GH_ENV:-aws-stag} / aws-prod); on a laptop pass it explicitly."
        ;;
    *)
        die "PROD must be one of: stag, true, lza-stag, lza (got '${PROD_TOKEN}').
   Those are the deploy/env_mode.mk tokens; set TF_PROD on the GitHub
   environment, or PROD on the make command line."
        ;;
esac

if [[ "${ENV}" == lza-prod || "${ENV}" == lza-stag ]]; then
    IS_LZA=1
else
    IS_LZA=""
fi

# The class assertion described in the usage block. It runs before the print-only
# path on purpose: a mismatched token should fail on the first invocation in a
# job, not on the one that writes the file.
if [[ -n "${EXPECTED_ENV_CLASS:-}" ]]; then
    if [[ "${EXPECTED_ENV_CLASS}" != "${ENV_CLASS_VALUE}" ]]; then
        die "PROD=${PROD_TOKEN} composes the ${ENV_CLASS_VALUE}-class estate (${ENV_FILE}), but this run expects ${EXPECTED_ENV_CLASS}.
   In CI the expectation comes from the branch (main -> prod, otherwise stag), so
   this is TF_PROD naming the wrong estate on the GitHub environment — not a reason
   to change the branch. Refusing to compose, because the dangerous direction is
   silent: a stag-grade token on prod plans the prod RDS with
   deletion_protection = false and skip_final_snapshot = true, unattended.
   Fix TF_PROD (${GH_ENV} expects one of: stag, true, lza-stag, lza)."
    fi
elif [[ -n "${GITHUB_ACTIONS:-}" ]]; then
    die "EXPECTED_ENV_CLASS is not set, and this is running in CI.
   Every workflow that composes this file passes it from the ref, so add
       EXPECTED_ENV_CLASS: \${{ github.ref_name == 'main' && 'prod' || 'stag' }}
   to the step's env (terraform_plan.yml passes the literal 'stag' — it only plans
   staging). Without it, a mis-set TF_PROD composes the wrong estate in silence,
   which is the whole point of the assertion; set it to '${ENV_CLASS_VALUE}' if
   ${PROD_TOKEN} is genuinely the intended mode for this job."
fi

DEFAULT_OUT_FILE="${REPO_ROOT}/${ENV_FILE}"

# `--print-env` prints the derived target and exits — no values needed. It exists
# so the table above, and the path the env file is written to, can be pinned
# against `deploy/env_mode.mk` and the Makefile by a test
# (tests/test_ci_env_target.py) instead of by reading the files side by side.
if [[ "${1:-}" == "--print-env" ]]; then
    printf 'ENV=%s\nENV_FILE_NAME=%s\nAWS_PROFILE=%s\nGH_ENV=%s\nENV_CLASS=%s\nOUT_FILE=%s\n' \
        "${ENV}" "${ENV_FILE}" "${AWS_PROFILE_VALUE}" "${GH_ENV}" "${ENV_CLASS_VALUE}" "${DEFAULT_OUT_FILE}"
    exit 0
fi

OUT_FILE="${1:-${DEFAULT_OUT_FILE}}"

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

    # The LZA keys (FLIP#749) are handled per mode below, not here: required on
    # the platform-managed estate, optional (and expected absent) on the
    # self-contained ones.
)

# ---------------------------------------------------------------------------
# The LZA keys (FLIP#749) — required on the platform-managed estate, optional
# (and expected absent) on the self-contained ones.
#
# CI drives both shapes now: FLIP's own stag/prod were repointed at LZA workload
# accounts (README, "Repointing CI at the LZA accounts"), so the same three
# workflows must compose a legacy env file and an LZA one. Rather than a second
# script, the PROD token selects which of these are load-bearing:
#
#   ACCESS_LOGS_BUCKET_NAME     — "" derives flip-access-logs-<flip_alb_subdomain>,
#                                 a bucket the legacy account owns
#   EFS_PROVISION_IMAGE         — the one-shot EFS-provision utility image; on LZA
#                                 it must come from the ecr-public/ pull-through
#                                 cache, because the account has no internet egress
#   LZA_VPC_NAME                — the accelerator-provisioned VPC's Name tag
#   MANAGE_DNS                  — false while the account has no Route 53 zone
#   NETWORKING_INGRESS_CIDRS    — the ingress-VPC CIDRs the estate's edge reaches
#                                 the internal FL NLB from; empty deletes every
#                                 rule they feed, so it is not defaultable
#   LZA_ELB_ACCESS_LOGS_BUCKET  — the LogArchive account's central access-logs
#                                 bucket (an accelerator guardrail)
#
# The two web-edge values are the exception in the other direction: the edge is
# built *from* this stack's outputs, so on a first apply the domain and ARN are
# legitimately empty (variables.tf documents the two-phase wiring). Optional, so
# they compose as omitted rather than as an empty string.
#
# On the legacy tokens these are optional for the reason they always were —
# absent is the legacy value in every case — and are still carried through the
# workflows, so the drift guard above keeps covering them.
LZA_REQUIRED_KEYS=(
    ACCESS_LOGS_BUCKET_NAME
    EFS_PROVISION_IMAGE
    LZA_VPC_NAME
    MANAGE_DNS
    LZA_ELB_ACCESS_LOGS_BUCKET
    NETWORKING_INGRESS_CIDRS
)
LZA_OPTIONAL_KEYS=(
    LZA_WEB_EDGE_DOMAIN
    LZA_WEB_EDGE_DISTRIBUTION_ARN
)

if [[ -n "${IS_LZA}" ]]; then
    REQUIRED_KEYS+=("${LZA_REQUIRED_KEYS[@]}")
    OPTIONAL_KEYS+=("${LZA_OPTIONAL_KEYS[@]}")
else
    OPTIONAL_KEYS+=("${LZA_REQUIRED_KEYS[@]}" "${LZA_OPTIONAL_KEYS[@]}")
fi

# The LZA inputs with no `export TF_VAR_…` line of their own in the Makefile: the
# env file is included as make syntax, so writing them as `export TF_VAR_<name>=…`
# is what carries them to Terraform — and to `make print-tf-env`, which greps the
# recipe's environment. Left of the colon is the manifest/GitHub key, right of it
# the Terraform variable. Data rather than two lists so the emit loop and the
# manifest cannot drift.
LZA_RAW_TF_VARS=(
    "NETWORKING_INGRESS_CIDRS:networking_ingress_cidrs"
    "LZA_ELB_ACCESS_LOGS_BUCKET:lza_elb_access_logs_bucket"
    "LZA_WEB_EDGE_DOMAIN:lza_web_edge_domain"
    "LZA_WEB_EDGE_DISTRIBUTION_ARN:lza_web_edge_distribution_arn"
)

# Those keys are emitted through the raw loop instead of the plain `KEY=value`
# form below, so they are skipped there: one key, one line in the file.
is_raw_tf_var() {
    local key="$1" pair
    for pair in "${LZA_RAW_TF_VARS[@]}"; do
        [[ "${pair%%:*}" == "${key}" ]] && return 0
    done
    return 1
}

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
        die "FL_BACKEND is not set — it selects which kit date is required. Set it in the ${GH_ENV} GitHub environment."
        ;;
    *)
        die "FL_BACKEND must be 'nvflare' or 'flower' (got '${FL_BACKEND}')"
        ;;
esac

# AWS_PROFILE is *not* a stored value, and in CI it does not name a profile at
# all. It exists solely to satisfy the Makefile's account guard, which refuses to
# parse unless AWS_PROFILE equals PROD_AWS_PROFILE / STAG_AWS_PROFILE /
# LZA_AWS_PROFILE / LZA_STAG_AWS_PROFILE — a guard written for laptops, where the
# profile really is how you choose an account.
#
# On a runner the account comes from the OIDC role that
# aws-actions/configure-aws-credentials has already assumed into AWS_ACCESS_KEY_ID
# and friends; no ~/.aws/config is written by any workflow, and nothing would read
# one if it were. The value never leaves the Makefile either: `make print-tf-env`
# emits only `TF_VAR_*` lines, so AWS_PROFILE does not reach the terraform steps.
#
# Deriving it from the PROD token (the table at the top) rather than storing it is
# still what stops a mis-set GitHub variable from pointing a stag run's Makefile
# at the prod branch of that guard — which is the only decision the value drives.

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
    echo "❌ Cannot compose ${OUT_FILE} for the '${ENV}' environment (PROD=${PROD_TOKEN})." >&2
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
   '${GH_ENV}', with the same value as the matching key in the operator's
   ${ENV_FILE} file. See deploy/providers/AWS/README.md,
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
    echo "# Environment: ${ENV} (PROD=${PROD_TOKEN}). Values come from the GitHub environment '${GH_ENV}'."
    echo "AWS_PROFILE=${AWS_PROFILE_VALUE}"
} >"${emit_to}"

written=0
for key in "${REQUIRED_KEYS[@]}" "${OPTIONAL_KEYS[@]}"; do
    value="${!key:-}"
    [[ -n "${value}" ]] || continue
    # Emitted through the raw TF_VAR_ loop below instead.
    is_raw_tf_var "${key}" && continue
    printf '%s=%s\n' "${key}" "$(escape_for_make "${value}")" >>"${emit_to}"
    written=$((written + 1))
done

# The LZA inputs the Makefile has no `export TF_VAR_…` line for. `include` reads
# the file as make syntax, so an `export TF_VAR_x=…` line here is a make variable
# that is exported to every recipe — which is exactly how the operator's own
# .env.lza-prod carries them (`variables.tf` documents the raw-export form), and
# what makes `make print-tf-env` see them.
for pair in "${LZA_RAW_TF_VARS[@]}"; do
    key="${pair%%:*}"
    tf_var="${pair##*:}"
    value="${!key:-}"
    [[ -n "${value}" ]] || continue
    printf 'export TF_VAR_%s=%s\n' "${tf_var}" "$(escape_for_make "${value}")" >>"${emit_to}"
    written=$((written + 1))
done

# Written with 0600 from the start: the file carries AES_KEY_BASE64 and the DB
# credentials, and a runner's workspace is world-readable by default.
install -m 600 "${emit_to}" "${OUT_FILE}"

echo "✅ Composed ${OUT_FILE} for '${ENV}' (PROD=${PROD_TOKEN}) — ${written} keys (${#REQUIRED_KEYS[@]} required, plus AWS_PROFILE)."
