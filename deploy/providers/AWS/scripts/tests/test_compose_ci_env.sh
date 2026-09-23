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

# Black-box tests for scripts/compose-ci-env.sh — the completeness guard, the
# Make-escaping round trip, and the manifest/Makefile drift check.
#
# Drives the REAL script in a clean environment (`env -i`), so a key the test did
# not set cannot leak in from the developer's shell. No credentials, no network.
#
# The drift case is the one that matters most in the long run: it reads the
# Makefile's own `export TF_VAR_…` lines and asserts every env key they interpolate
# appears in the script's manifest. Without it, adding a TF_VAR export is a silent
# way to make CI plan from an empty string months later.
#
# Usage:
#     bash deploy/providers/AWS/scripts/tests/test_compose_ci_env.sh

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AWS_DIR="$(cd "${HERE}/../.." && pwd)"
REPO_ROOT="$(cd "${AWS_DIR}/../../.." && pwd)"
SCRIPT="${AWS_DIR}/scripts/compose-ci-env.sh"
MAKEFILE="${AWS_DIR}/Makefile"
WORKFLOW_DIR="${REPO_ROOT}/.github/workflows"

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

# A complete, valid value set. Cases copy this and mutate one key, so each test
# asserts the effect of exactly one difference.
#
# PROD is the deploy/env_mode.mk token the script now takes (it used to be
# TF_ENV). The LZA keys are deliberately absent here: absent is the correct value
# on the legacy tokens, and the LZA cases below add them.
BASE_ENV=(
    PROD=stag
    AWS_REGION=eu-west-2
    FLIP_TFSTATE_BUCKET_NAME=flip-terraform-state-stag
    VPC_NAME=flip-vpc
    AICENTRE_BUCKET_NAME=aicentre-stag
    FLIP_APP_BUNDLES_BUCKET_NAME=flip-app-bundles-stag
    FLIP_FL_RESULTS_BUCKET_NAME=flip-fl-results-stag
    FLIP_MODEL_FILES_UPLOADS_BUCKET_NAME=flip-model-files-stag
    FLIP_UI_BUCKET_NAME=flip-ui-stag
    ADMIN_USER_PASSWORD=not-a-real-password
    AES_KEY_BASE64=bm90LWEtcmVhbC1rZXk=
    INTERNAL_SERVICE_KEY=not-a-real-service-key
    INTERNAL_SERVICE_KEY_HASH=0000000000000000000000000000000000000000000000000000000000000000
    POSTGRES_DB=flip
    POSTGRES_USER=flipuser
    API_PORT=8000
    DB_PORT=5432
    ENFORCE_MFA=true
    FL_ADMIN_DIRECTORY=/workspace
    FL_API_PORT=8080
    FL_SERVER_PORT=8002
    INTERNAL_SERVICE_KEY_HEADER=X-Internal-Service-Key
    SES_VERIFIED_EMAIL=noreply@example.invalid
    TRUST_API_KEY_HEADER=X-Trust-API-Key
    UI_PORT=80
    ALB_SUBDOMAIN=api-stag
    NLB_SUBDOMAIN=fl-stag
    DOCKER_REGISTRY=ghcr.io/londonaicentre/
    DOCKER_TAG=stag
    DOCKER_FL_TAG=stag
    FL_BACKEND=nvflare
    FL_KIT_SLOT_NAMES='["Trust_1", "Trust_2"]'
    FLARE_KIT_DATE=20260429
    # Required, not optional: their Makefile `?=` defaults create a trust host
    # and delete the trust NLB ingress rules respectively.
    DEPLOY_TRUST_EC2=false
    LOCAL_TRUST_PUBLIC_IPS='["203.0.113.10"]'
    K8S_TRUST_PUBLIC_IPS=[]
)

# The keys a platform-managed (LZA) estate adds on top of BASE_ENV (FLIP#749).
# Required on the two LZA tokens, expected absent on the legacy ones — see
# section 14. Held here beside BASE_ENV because both the AWS_PROFILE cases and
# the LZA section need a value set that composes.
LZA_ENV=(
    ACCESS_LOGS_BUCKET_NAME=flip-access-logs-lza
    EFS_PROVISION_IMAGE=123456789012.dkr.ecr.eu-west-2.amazonaws.com/ecr-public/aws-cli/aws-cli:2.22.35
    LZA_VPC_NAME=AWSAccelerator-eu-west-2-prod
    MANAGE_DNS=false
    LZA_ELB_ACCESS_LOGS_BUCKET=central-elb-access-logs
    NETWORKING_INGRESS_CIDRS='["10.12.0.0/24"]'
)

# Run the script under `env -i` with BASE_ENV plus any overrides given as
# KEY=VALUE arguments. A bare KEY (no `=`) removes that key from the set.
# Sets: RC, STDOUT, STDERR, OUT_FILE.
run_case() {
    local title="$1"
    shift
    echo ""
    echo "-- ${title}"

    local -a final=("${BASE_ENV[@]}")
    local override key
    for override in "$@"; do
        key="${override%%=*}"
        local -a next=()
        local existing
        for existing in "${final[@]}"; do
            [[ "${existing%%=*}" == "${key}" ]] || next+=("${existing}")
        done
        [[ "${override}" == *=* ]] && next+=("${override}")
        final=("${next[@]}")
    done

    OUT_FILE="${TEST_ROOT}/composed-$$-${RANDOM}.env"
    rm -f "${OUT_FILE}"
    local err="${TEST_ROOT}/stderr"
    # PATH is needed for mktemp/install/grep; HOME for nothing here but keeps
    # bash quiet. Everything else comes from `final` only.
    STDOUT="$(env -i PATH="${PATH}" HOME="${TEST_ROOT}" "${final[@]}" \
        bash "${SCRIPT}" "${OUT_FILE}" 2>"${err}")"
    RC=$?
    STDERR="$(cat "${err}")"
}

expect_rc() {
    local want="$1" what="$2"
    if [[ "${RC}" -eq "${want}" ]]; then
        ok "${what}"
    else
        no "${what}" "wanted exit ${want}, got ${RC}" "stderr: ${STDERR}"
    fi
}

expect_stderr() {
    local needle="$1" what="$2"
    if [[ "${STDERR}" == *"${needle}"* ]]; then
        ok "${what}"
    else
        no "${what}" "stderr did not contain: ${needle}" "stderr: ${STDERR}"
    fi
}

# Read a key back out of the composed file the way the real Makefile does: include
# it, re-export it through a `TF_VAR_…=${KEY}` line exactly as deploy/providers/AWS/Makefile
# does, and read the result out of the recipe's *environment*. Going through `env`
# rather than interpolating $(KEY) into a shell command is deliberate — the shell
# would strip quotes and expand `$word` itself, so an interpolated probe reports
# mangling the pipeline never actually suffers, and hides mangling it does.
make_value_of() {
    local key="$1" file="$2"
    local mk="${TEST_ROOT}/probe.mk"
    cat >"${mk}" <<EOF
include ${file}
export TF_VAR_probe=\${${key}}
probe:
	@env | sed -n 's/^TF_VAR_probe=//p'
EOF
    make --no-print-directory -f "${mk}" probe 2>/dev/null
}

echo "==== compose-ci-env.sh ===="

# 1. DRIFT GUARD. Every env key the Makefile interpolates into a TF_VAR export
#    must be in the script's manifest. The reverse is deliberately not asserted:
#    the manifest also carries keys the Makefile consumes outside a TF_VAR export
#    (FLIP_TFSTATE_BUCKET_NAME feeds `init -backend-config`).
echo ""
echo "-- manifest covers every TF_VAR input in the Makefile"
# Derived inside deploy/fl_backend.mk from FL_BACKEND (the DOCKER_FL_* names) and
# inside deploy/env_mode.mk from PROD (ENV_CLASS, IS_LZA), never stored.
# HOME and PROD are supplied by the runner and by make itself.
NOT_STORED='^(DOCKER_FL_API_NAME|DOCKER_FL_SERVER_NAME|DOCKER_FL_CLIENT_NAME|ENV_CLASS|IS_LZA|HOME|PROD)$'
# The manifest is the two array literals plus the per-backend `REQUIRED_KEYS+=(…)`
# / `OPTIONAL_KEYS+=(…)` additions made once FL_BACKEND is known.
manifest="$( {
    grep -oE '^[[:space:]]+[A-Z][A-Z0-9_]*$' "${SCRIPT}" | sed -E 's/^[[:space:]]+//'
    grep -oE '(REQUIRED|OPTIONAL)_KEYS\+=\([A-Z0-9_]+\)' "${SCRIPT}" |
        sed -E 's/.*\(([A-Z0-9_]+)\)/\1/'
} | LC_ALL=C sort -u)"
referenced="$(grep '^export TF_VAR_' "${MAKEFILE}" |
    grep -oE '\$\{[A-Za-z_][A-Za-z0-9_]*\}|\$\([A-Za-z_][A-Za-z0-9_]*\)' |
    tr -d '${}()' | grep -vE "${NOT_STORED}" | LC_ALL=C sort -u)"
uncovered="$(LC_ALL=C comm -23 <(echo "${referenced}") <(echo "${manifest}"))"
if [[ -z "${uncovered}" ]]; then
    ok "every Makefile TF_VAR input is in the manifest ($(echo "${referenced}" | wc -l) keys)"
else
    no "every Makefile TF_VAR input is in the manifest" \
        "not in the manifest — add to compose-ci-env.sh AND to both GitHub environments:" \
        ${uncovered}
fi

# 1b. The CI workflows must pass every manifest key through to the script. They
#     are the only place `vars` and `secrets` resolve, so the mapping cannot be
#     factored out — which makes silent omission the obvious failure. A missing
#     key here does fail loudly at compose time, but it fails in a deploy run;
#     catching it in this harness costs nothing and fails in the PR instead.
#
#     Keys the workflow supplies by other means are excluded: the FL_KIT_DATE
#     selectors are computed, and the apply workflow deliberately omits
#     DOCKER_TAG / DOCKER_FL_TAG so the resolved sha tags from
#     resolve-image-tags.sh are inherited rather than overridden (hazard A). PROD
#     is not a manifest key at all — it is the selector, checked separately below.
#
#     deploy_ui.yml composes through the same script (#1186) — it is the file the
#     UI's window.js is generated from, so it has to be the same file — and is
#     covered by the same rule.
for wf in terraform_plan.yml terraform_apply.yml terraform_drift.yml deploy_ui.yml; do
    echo ""
    echo "-- ${wf} passes every manifest key through"
    wf_path="${WORKFLOW_DIR}/${wf}"
    if [[ ! -f "${wf_path}" ]]; then
        no "${wf} exists" "not found at ${wf_path}"
        continue
    fi
    # Keys appearing as `KEY: ${{ vars.X }}` / `${{ secrets.X }}` in the workflow.
    wf_keys="$(grep -oE '^[[:space:]]+[A-Z][A-Z0-9_]*:[[:space:]]+\$\{\{[[:space:]]*(vars|secrets)\.' "${wf_path}" |
        sed -E 's/^[[:space:]]+([A-Z0-9_]+):.*/\1/' | LC_ALL=C sort -u)"
    # DOCKER_TAG / DOCKER_FL_TAG are exempt in all three: every workflow now runs
    # resolve-image-tags.sh and inherits its output through $GITHUB_ENV, so a
    # `vars.DOCKER_TAG` line in the compose step would override the resolved tag
    # and put the mutable one back. The assertion below requires the resolver
    # instead, so the exemption cannot be used to simply drop the key.
    exempt='^(FLARE_KIT_DATE|FLOWER_KIT_DATE|DOCKER_TAG|DOCKER_FL_TAG)$'
    wanted="$(echo "${manifest}" | grep -vE "${exempt}")"
    absent="$(LC_ALL=C comm -23 <(echo "${wanted}") <(echo "${wf_keys}"))"
    if [[ -z "${absent}" ]]; then
        ok "every manifest key is wired into ${wf}"
    else
        no "every manifest key is wired into ${wf}" \
            "not passed through — the workflow would fail the compose step:" \
            ${absent}
    fi
done

# 1c. EVERY WORKFLOW MUST READ THE MODE TOKEN IN BOTH STEPS THAT NEED IT.
#
#     TF_PROD is the only mode input, and two steps consume it: the compose step
#     (which writes .env.<ENV>) and the make step that resolves TF_VAR_* (which
#     includes .env.<ENV>). They have to agree, or a run composes one
#     environment's file and then reads another's — and because the Makefile's
#     env-file include is wildcard-guarded, a missing file is skipped silently
#     and Terraform sees an empty input set. Two identical lines per workflow, and
#     each must consult vars.TF_PROD.
echo ""
echo "-- every workflow reads the mode token in both steps"
token_offenders=""
for wf in terraform_plan.yml terraform_apply.yml terraform_drift.yml deploy_ui.yml; do
    wf_path="${WORKFLOW_DIR}/${wf}"
    [[ -f "${wf_path}" ]] || continue
    prod_lines="$(grep -oE '^[[:space:]]+PROD:[[:space:]]+.*$' "${wf_path}" | sed -E 's/^[[:space:]]+PROD:[[:space:]]+//')"
    count="$(printf '%s\n' "${prod_lines}" | grep -c .)"
    distinct="$(printf '%s\n' "${prod_lines}" | grep . | LC_ALL=C sort -u | wc -l)"
    vars_refs="$(printf '%s\n' "${prod_lines}" | grep -c 'vars\.TF_PROD')"
    if [[ "${count}" -ge 2 && "${distinct}" -eq 1 && "${vars_refs}" -eq "${count}" ]]; then
        continue
    fi
    token_offenders="${token_offenders} ${wf}(lines=${count},distinct=${distinct},from-tf-prod=${vars_refs})"
done
if [[ -z "${token_offenders}" ]]; then
    ok "all three select PROD from TF_PROD, identically in every step"
else
    no "all three select PROD from TF_PROD, identically in every step" \
        "compose and make would disagree about the environment:" ${token_offenders}
fi

# 1d. EVERY WORKFLOW MUST RESOLVE THE IMAGE TAGS, AND NONE MAY OVERRIDE THEM.
#
#     `vars.DOCKER_TAG` is the mutable `:stag` / `:prod`; an apply writes the
#     immutable `sha-<short7>` pin (FLIP#751) into the task definitions. A plan or
#     drift run that read the configured value would report a permanent
#     `sha-… -> :prod` diff on the FL task definitions — which the apply's own FL
#     gate then holds every apply on. So all three run resolve-image-tags.sh, and
#     the exemption above is only safe while they do.
echo ""
echo "-- every Terraform CI workflow resolves the image tags"
resolver_missing=""
override_offenders=""
for wf in terraform_plan.yml terraform_apply.yml terraform_drift.yml; do
    wf_path="${WORKFLOW_DIR}/${wf}"
    [[ -f "${wf_path}" ]] || continue
    grep -q 'resolve-image-tags.sh' "${wf_path}" || resolver_missing="${resolver_missing} ${wf}"
    # `FALLBACK_DOCKER_TAG: ${{ vars.DOCKER_TAG }}` is how the resolver is *fed*
    # and is fine; a bare `DOCKER_TAG:` line is the override that is not.
    if grep -qE '^[[:space:]]+DOCKER_(FL_)?TAG:[[:space:]]+\$\{\{' "${wf_path}"; then
        override_offenders="${override_offenders} ${wf}"
    fi
done
if [[ -z "${resolver_missing}" ]]; then
    ok "all three run resolve-image-tags.sh"
else
    no "all three run resolve-image-tags.sh" \
        "these would plan against the mutable tag:" ${resolver_missing}
fi
if [[ -z "${override_offenders}" ]]; then
    ok "none re-supplies DOCKER_TAG / DOCKER_FL_TAG from vars"
else
    no "none re-supplies DOCKER_TAG / DOCKER_FL_TAG from vars" \
        "this overrides the resolved tag and reintroduces the un-pin:" ${override_offenders}
fi

# 1e. NO WORKFLOW MAY PUBLISH A PLAN FILE AS AN ARTIFACT.
#
#     tf-via-pr defaults upload-plan to true, and a Terraform plan file is a zip
#     containing the full tfstate — AES_KEY_BASE64, INTERNAL_SERVICE_KEY and
#     ADMIN_USER_PASSWORD in plaintext. This repository is PUBLIC, so an uploaded
#     plan is a public download of live credentials. It happened once: the first
#     green CI plan (2026-09-01) left a 357 KB stag plan artifact behind, because
#     omitting the input takes the insecure default.
#
#     Nothing needs the artifact. terraform_plan.yml only comments the diff, and
#     terraform_apply.yml re-plans from scratch. `preserve-plan` is a different
#     input — it keeps the file on the runner for the FL gate — and stays true.
echo ""
echo "-- no workflow uploads a plan file as an artifact"
upload_offenders=""
for wf in terraform_plan.yml terraform_apply.yml terraform_drift.yml; do
    wf_path="${WORKFLOW_DIR}/${wf}"
    [[ -f "${wf_path}" ]] || continue
    grep -q 'tf-via-pr' "${wf_path}" || continue
    # Every tf-via-pr step must carry an explicit `upload-plan: false`; relying
    # on the default is the bug this guards.
    steps="$(grep -c 'uses: op5dev/tf-via-pr' "${wf_path}")"
    disabled="$(grep -cE '^[[:space:]]+upload-plan:[[:space:]]*false[[:space:]]*$' "${wf_path}")"
    if [[ "${disabled}" -lt "${steps}" ]]; then
        upload_offenders="${upload_offenders} ${wf}(${disabled}/${steps})"
    fi
done
if [[ -z "${upload_offenders}" ]]; then
    ok "every tf-via-pr step sets upload-plan: false"
else
    no "every tf-via-pr step sets upload-plan: false" \
        "a plan artifact on a public repo publishes tfstate in plaintext:" \
        ${upload_offenders}
fi

# 2. HAPPY PATH.
run_case "complete value set composes" 
expect_rc 0 "exits 0"
if [[ -f "${OUT_FILE}" ]]; then
    ok "writes the file"
else
    no "writes the file"
fi
if [[ "$(stat -c '%a' "${OUT_FILE}" 2>/dev/null)" == "600" ]]; then
    ok "file is 0600 (it carries AES_KEY_BASE64 and the DB user)"
else
    no "file is 0600" "mode: $(stat -c '%a' "${OUT_FILE}" 2>/dev/null)"
fi

# 3. AWS_PROFILE is derived, not stored — this is what stops a mis-set GitHub
#    variable from pointing a stag run at the prod account. The token-to-profile
#    mapping is the one the Makefile's guard expects for that ENV.
if grep -qx 'AWS_PROFILE=stag' "${OUT_FILE}"; then
    ok "derives AWS_PROFILE=stag from the stag token"
else
    no "derives AWS_PROFILE=stag from the stag token" "file: $(grep '^AWS_PROFILE' "${OUT_FILE}")"
fi
run_case "prod environment" PROD=true
if grep -qx 'AWS_PROFILE=prod' "${OUT_FILE}"; then
    ok "derives AWS_PROFILE=prod from the true token"
else
    no "derives AWS_PROFILE=prod from the true token"
fi
# The LZA pair: the LZA_AWS_PROFILE / LZA_STAG_AWS_PROFILE knobs in the Makefile,
# so a JIT-bound run passes the same guard a laptop does.
run_case "lza staging environment" PROD=lza-stag "${LZA_ENV[@]}"
if grep -qx 'AWS_PROFILE=lza-stag' "${OUT_FILE}"; then
    ok "derives AWS_PROFILE=lza-stag from the lza-stag token"
else
    no "derives AWS_PROFILE=lza-stag from the lza-stag token" "file: $(grep '^AWS_PROFILE' "${OUT_FILE}")"
fi
run_case "lza prod environment" PROD=lza "${LZA_ENV[@]}"
if grep -qx 'AWS_PROFILE=lza-prod' "${OUT_FILE}"; then
    ok "derives AWS_PROFILE=lza-prod from the lza token"
else
    no "derives AWS_PROFILE=lza-prod from the lza token"
fi

# 4. UNSET OPTIONAL KEYS ARE OMITTED, not written empty. The Makefile guards
#    JOB_RESOURCE_SPEC_* behind `ifneq (…,)` precisely so an unset value falls
#    back to the variables.tf default; writing `KEY=` here would defeat that and
#    make Terraform reject an empty string for a number variable.
run_case "unset optional keys are omitted"
if grep -q '^JOB_RESOURCE_SPEC_NUM_GPUS=' "${OUT_FILE}"; then
    no "omits unset JOB_RESOURCE_SPEC_NUM_GPUS" "file has: $(grep '^JOB_RESOURCE' "${OUT_FILE}")"
else
    ok "omits unset JOB_RESOURCE_SPEC_NUM_GPUS rather than writing it empty"
fi
run_case "set optional keys are passed through" JOB_RESOURCE_SPEC_NUM_GPUS=1
if grep -qx 'JOB_RESOURCE_SPEC_NUM_GPUS=1' "${OUT_FILE}"; then
    ok "passes through a set optional key"
else
    no "passes through a set optional key"
fi

# 5. MISSING REQUIRED KEY. The headline failure this script exists to prevent.
run_case "missing required key fails loudly" AES_KEY_BASE64
expect_rc 1 "exits 1"
expect_stderr "AES_KEY_BASE64" "names the missing key"
expect_stderr "aws-stag" "points at the GitHub environment to fix"
if [[ -f "${OUT_FILE}" ]]; then
    no "writes no file on failure" "a partial file would let make proceed with an empty value"
else
    ok "writes no file on failure"
fi

# 5b. THE DESTRUCTIVE DEFAULTS. `?=` in the Makefile means an absent key does not
#     reach variables.tf at all — it reaches Terraform as the Makefile's own
#     default, and for these three that default destroys something: a trust host
#     appears on stag, or every on-prem/K8s NLB ingress rule is deleted. None of
#     them is on check-fl-plan-impact.sh's watch list, so an unattended apply
#     would do it silently. Required, so the value has to be written down.
for key in DEPLOY_TRUST_EC2 LOCAL_TRUST_PUBLIC_IPS K8S_TRUST_PUBLIC_IPS; do
    run_case "${key} is required, not defaulted" "${key}"
    expect_rc 1 "exits 1"
    expect_stderr "${key}" "names it"
done

# ...and `[]` / `false` are legitimate values that must compose, not be mistaken
# for absence. This is the whole point of requiring them: an explicit empty list
# is a decision, an absent key is an accident.
run_case "an explicit empty list composes" LOCAL_TRUST_PUBLIC_IPS='[]'
expect_rc 0 "exits 0"
if grep -qFx 'LOCAL_TRUST_PUBLIC_IPS=[]' "${OUT_FILE}"; then
    ok "writes the explicit empty list through"
else
    no "writes the explicit empty list through" "file: $(grep '^LOCAL_TRUST' "${OUT_FILE}")"
fi

# 6. ALL missing keys are reported at once — a one-at-a-time script costs a CI
#    round trip per key.
run_case "reports every missing key at once" AES_KEY_BASE64 POSTGRES_USER VPC_NAME
expect_stderr "Missing or empty (3)" "reports the full count"
expect_stderr "POSTGRES_USER" "names the second"
expect_stderr "VPC_NAME" "names the third"

# 7. SECRETS ARE NEVER ECHOED.
run_case "failure output carries no values" VPC_NAME
if [[ "${STDERR}${STDOUT}" == *"not-a-real-password"* || "${STDERR}${STDOUT}" == *"bm90LWEtcmVhbC1rZXk="* ]]; then
    no "no secret value appears in the output" "output leaked a value into the workflow log"
else
    ok "no secret value appears in the output"
fi

# 8. PLACEHOLDER. A copied-but-unedited env file leaves `<your-bucket>` in place;
#    non-empty, so an emptiness check passes it to Terraform and fails opaquely later.
run_case "unedited placeholder is rejected" AICENTRE_BUCKET_NAME='<your-bucket-name>'
expect_rc 1 "exits 1"
expect_stderr "unedited <placeholder>" "reports it as a placeholder, not as missing"
expect_stderr "AICENTRE_BUCKET_NAME" "names the key"

# 9. MALFORMED VALUES. Make silently keeps trailing whitespace (it strips leading),
#    and cannot carry an embedded newline at all.
run_case "trailing whitespace is rejected" 'VPC_NAME=flip-vpc '
expect_rc 1 "exits 1"
expect_stderr "trailing whitespace" "explains why"
run_case "embedded newline is rejected" 'ADMIN_USER_PASSWORD=one
two'
expect_rc 1 "exits 1"
expect_stderr "ADMIN_USER_PASSWORD" "names the key"
# A value ending in a backslash continues the line in Make and swallows the next
# key outright; `\#` inside a value cannot be escaped at all. Neither is worth
# half-handling.
run_case "backslash is rejected" 'ADMIN_USER_PASSWORD=back\\slash'
expect_rc 1 "exits 1"
expect_stderr "backslash" "explains why"

# 10. MAKE ESCAPING ROUND TRIP. A generated password containing `#` or `$` is the
#     realistic case: unescaped, Make truncates at `#` (comment) and eats `$`
#     (variable reference), and Terraform receives a silently different secret.
run_case "hash and dollar survive the Make round trip" 'ADMIN_USER_PASSWORD=pa#ss$word'
expect_rc 0 "exits 0"
got="$(make_value_of ADMIN_USER_PASSWORD "${OUT_FILE}")"
if [[ "${got}" == 'pa#ss$word' ]]; then
    ok "make reads back the exact value"
else
    no "make reads back the exact value" "make saw a different value than was supplied"
fi

# 11. The HCL list literal in FL_KIT_SLOT_NAMES must survive verbatim — it is
#     handed to Terraform as a list, not a string.
run_case "HCL list literal survives"
got="$(make_value_of FL_KIT_SLOT_NAMES "${OUT_FILE}")"
if [[ "${got}" == '["Trust_1", "Trust_2"]' ]]; then
    ok "FL_KIT_SLOT_NAMES round-trips through make"
else
    no "FL_KIT_SLOT_NAMES round-trips through make" "make saw: ${got}"
fi

# 12. FL_BACKEND selects which kit date is required. Requiring both would block a
#     deployment that legitimately runs one backend; requiring neither reproduces
#     the Makefile's own opaque late failure.
run_case "nvflare requires FLARE_KIT_DATE" FLARE_KIT_DATE
expect_rc 1 "exits 1"
expect_stderr "FLARE_KIT_DATE" "names it"
run_case "flower requires FLOWER_KIT_DATE instead" FL_BACKEND=flower FLARE_KIT_DATE
expect_rc 1 "exits 1"
expect_stderr "FLOWER_KIT_DATE" "requires the flower date"
if [[ "${STDERR}" == *"FLARE_KIT_DATE"* ]]; then
    no "does not require the other backend's date" "FLARE_KIT_DATE demanded while FL_BACKEND=flower"
else
    ok "does not require the other backend's date"
fi
run_case "flower composes with FLOWER_KIT_DATE" FL_BACKEND=flower FLARE_KIT_DATE FLOWER_KIT_DATE=20260501
expect_rc 0 "exits 0"

# 13. Guard the two selectors themselves.
run_case "unknown FL_BACKEND is rejected" FL_BACKEND=jax
expect_rc 1 "exits 1"
expect_stderr "must be 'nvflare' or 'flower'" "explains the valid set"
run_case "unknown PROD token is rejected" PROD=dev
expect_rc 1 "exits 1"
expect_stderr "PROD must be one of" "explains the valid set"
# Unset is not a mode: it used to default to staging, which is exactly how a run
# could compose for the wrong estate without anyone choosing one.
run_case "an unset PROD is rejected rather than defaulted" PROD
expect_rc 1 "exits 1"
expect_stderr "PROD is not set" "says so"

# 14. THE LZA MODES (FLIP#749). Same three workflows and the same script — the
#     token decides which keys a platform-managed estate needs, and the four that
#     have no `export TF_VAR_…` line in the Makefile are written as raw exports.
echo ""
echo "-- the LZA tokens require the LZA keys"
run_case "lza-stag composes with the LZA key set" PROD=lza-stag "${LZA_ENV[@]}"
expect_rc 0 "exits 0"
if grep -qxF 'AWS_PROFILE=lza-stag' "${OUT_FILE}"; then
    ok "composes for the lza-stag environment"
else
    no "composes for the lza-stag environment" "file: $(grep '^AWS_PROFILE' "${OUT_FILE}")"
fi
# The raw-export form is what carries these to Terraform without a Makefile line
# of their own; `make_value_of` reads the make variable back, which is exactly
# what `print-tf-env`'s `env | grep '^TF_VAR_'` sees.
if grep -qxF 'export TF_VAR_networking_ingress_cidrs=["10.12.0.0/24"]' "${OUT_FILE}"; then
    ok "writes NETWORKING_INGRESS_CIDRS as a raw TF_VAR_ export"
else
    no "writes NETWORKING_INGRESS_CIDRS as a raw TF_VAR_ export" \
        "file: $(grep 'TF_VAR_networking' "${OUT_FILE}" || echo 'no raw export line')"
fi
got="$(make_value_of TF_VAR_networking_ingress_cidrs "${OUT_FILE}")"
if [[ "${got}" == '["10.12.0.0/24"]' ]]; then
    ok "the raw export round-trips through make as a list literal"
else
    no "the raw export round-trips through make as a list literal" "make saw: ${got}"
fi
# ...and the key must NOT also appear in the plain `KEY=value` form: one key, one
# line in the file.
if grep -qxF 'NETWORKING_INGRESS_CIDRS=["10.12.0.0/24"]' "${OUT_FILE}"; then
    no "does not write the raw keys twice" "the plain form is present as well"
else
    ok "does not write the raw keys twice"
fi

# The destructive default, in the same spirit as DEPLOY_TRUST_EC2 above: an absent
# NETWORKING_INGRESS_CIDRS is not "no change", it is every rule the estate's edge
# reaches the internal NLB through.
for key in ACCESS_LOGS_BUCKET_NAME EFS_PROVISION_IMAGE LZA_VPC_NAME MANAGE_DNS LZA_ELB_ACCESS_LOGS_BUCKET NETWORKING_INGRESS_CIDRS; do
    run_case "on lza-stag, ${key} is required" PROD=lza-stag "${LZA_ENV[@]}" "${key}"
    expect_rc 1 "exits 1"
    expect_stderr "${key}" "names it"
done

# The two web-edge values are the opposite case: the edge is built from this
# stack's outputs, so empty — omitted — is correct on a first apply.
run_case "lza composes with the web-edge values empty" PROD=lza "${LZA_ENV[@]}"
expect_rc 0 "exits 0"

# Requiring them on every token would block a self-contained deployment that
# shares this script, and the legacy value for all of them is absent.
run_case "the legacy tokens do not require the LZA keys" PROD=stag
expect_rc 0 "exits 0"
if grep -q 'TF_VAR_' "${OUT_FILE}"; then
    no "writes no raw TF_VAR_ lines for a legacy environment" "file carries: $(grep 'TF_VAR_' "${OUT_FILE}")"
else
    ok "writes no raw TF_VAR_ lines for a legacy environment"
fi

# 15. THE UI PUBLISH (#1186).
#
#     deploy_ui.yml publishes flip-ui on merge, and the properties worth pinning
#     are the ones that make it safe to run unattended: it publishes the same
#     recipe a human would (so the two cannot drift), it never names the bucket or
#     the distribution (FLIP#749 renames them — deploy-ui.sh reads the outputs), it
#     cannot assume the apply role, and it does not cancel a publish halfway
#     through an `s3 sync --delete`.
echo ""
echo "-- the flip-ui publish workflow"

UI_WF="${WORKFLOW_DIR}/deploy_ui.yml"
if [[ -f "${UI_WF}" ]]; then
    # The checks below are about what the workflow *does*, and a comment that says
    # "this runs deploy-ui.sh rather than an `aws s3 sync`" must not read as the
    # thing it is denying — so comments are stripped first.
    ui_code="$(grep -vE '^[[:space:]]*#' "${UI_WF}")"

    if grep -q 'scripts/deploy-ui.sh' <<<"${ui_code}" &&
        ! grep -qE 'aws s3 (sync|cp)|cloudfront create-invalidation' <<<"${ui_code}"; then
        ok "runs the shared deploy-ui.sh rather than its own copy of the recipe"
    else
        no "runs the shared deploy-ui.sh rather than its own copy of the recipe" \
            "$(grep -nE 'aws s3 |cloudfront create-invalidation|deploy-ui.sh' <<<"${ui_code}" | head -5)"
    fi

    # A bucket name or a distribution id in the YAML is the failure #749 exists to
    # prevent: it survives the account move as a name that no longer resolves.
    if ! grep -qE 's3://|--distribution-id|FlipUiBucketName|CloudfrontDistributionId' <<<"${ui_code}"; then
        ok "names no bucket and no distribution — both come from Terraform outputs"
    else
        no "names no bucket and no distribution" \
            "$(grep -nE 's3://|--distribution-id|FlipUiBucketName|CloudfrontDistributionId' <<<"${ui_code}")"
    fi

    # The apply role is pinned to terraform_apply.yml, and its whole safety argument
    # is that nothing else can assume it.
    if grep -q 'vars.UI_DEPLOY_ROLE_ARN' <<<"${ui_code}" && ! grep -q 'TF_APPLY_ROLE_ARN' <<<"${ui_code}"; then
        ok "assumes the bundle publisher's role, never the apply role"
    else
        no "assumes the bundle publisher's role, never the apply role" \
            "$(grep -nE 'ROLE_ARN' <<<"${ui_code}")"
    fi

    # An interrupted `s3 sync --delete` leaves a bucket matching neither release.
    if grep -qE '^  cancel-in-progress: false' <<<"${ui_code}"; then
        ok "never cancels a publish in progress"
    else
        no "never cancels a publish in progress" "$(grep -n 'cancel-in-progress' <<<"${ui_code}")"
    fi

    # The env-file name is asked of compose-ci-env.sh, not spelled out: the two
    # would otherwise drift silently, and the composed file would be ignored.
    if grep -q -- '--print-env' <<<"${ui_code}" && ! grep -qE '"?\$?\{?\{? *\.env\.(stag|production|lza)' <<<"${ui_code}"; then
        ok "resolves the env file through the compose script's --print-env"
    else
        no "resolves the env file through the compose script's --print-env" \
            "$(grep -nE '\-\-print-env|\.env\.(stag|production|lza)' <<<"${ui_code}")"
    fi

    # The two triggers, and the one thing they must agree on: workflow_run matches
    # the upstream workflow by NAME, so a rename in terraform_apply.yml silently
    # turns this into a workflow that never fires.
    upstream="$(grep -E '^name:' "${WORKFLOW_DIR}/terraform_apply.yml" | sed -E 's/^name:[[:space:]]*//')"
    if grep -qE "^[[:space:]]+workflows: \[\"${upstream}\"\]" <<<"${ui_code}"; then
        ok "waits on the apply workflow by its actual name ('${upstream}')"
    else
        no "waits on the apply workflow by its actual name" \
            "terraform_apply.yml is named '${upstream}':" \
            "$(grep -nA3 'workflow_run:' <<<"${ui_code}")"
    fi

    if grep -qE '^[[:space:]]+- "flip-ui/\*\*"' <<<"${ui_code}" &&
        grep -qE '^[[:space:]]+types: \[completed\]' <<<"${ui_code}"; then
        ok "fires on a flip-ui push and on a completed apply"
    else
        no "fires on a flip-ui push and on a completed apply" "$(grep -nE 'flip-ui|types:' <<<"${ui_code}")"
    fi
else
    no "deploy_ui.yml exists" "not found at ${UI_WF}"
fi

echo ""
echo "==== ${PASS} passed, ${FAIL} failed ===="
[[ "${FAIL}" -eq 0 ]]
