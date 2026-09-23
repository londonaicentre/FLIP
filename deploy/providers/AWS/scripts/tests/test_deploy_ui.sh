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
# Black-box tests for scripts/deploy-ui.sh — the recipe that publishes the
# flip-ui bundle, shared by `make deploy-ui` (a laptop) and
# .github/workflows/deploy_ui.yml (CI, on merge).
#
# The REAL script runs, against a synthetic repository — a copy of it at
# <tmp>/repo/deploy/providers/AWS/scripts/, plus the flip-ui tree it insists on —
# with `terraform`, `aws`, `npm`, `node` and `make` stubbed on PATH, each logging
# its argv. What is under test is the shape of what it runs, which no green CI run
# makes visible and which is all load-bearing:
#
#   * the bucket and distribution id come from `terraform output`, never from a
#     name in the script (FLIP#749 renames them);
#   * the cache-control split (hashed bundles immutable; index.html and
#     js/window.js no-cache) and the ark_demo/* exclusion that keeps --delete
#     from removing the public demo under the same distribution;
#   * the LZA branch, where there is no workload distribution to invalidate;
#   * that it never unsets the AWS credentials — CI's identity is those
#     credentials, while the laptop path is the caller's `$(_AWS_ENV)`.
#
# Usage:
#     bash deploy/providers/AWS/scripts/tests/test_deploy_ui.sh

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AWS_DIR="$(cd "${HERE}/../.." && pwd)"
REPO_ROOT="$(cd "${AWS_DIR}/../../.." && pwd)"
SCRIPT="${AWS_DIR}/scripts/deploy-ui.sh"
MAKEFILE="${AWS_DIR}/Makefile"
WORKFLOW="${REPO_ROOT}/.github/workflows/deploy_ui.yml"

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

FAKE="${TEST_ROOT}/repo"
BIN="${TEST_ROOT}/bin"
LOG="${TEST_ROOT}/calls.log"
mkdir -p "${FAKE}/deploy/providers/AWS/scripts" "${FAKE}/flip-ui/scripts" "${BIN}"

# A real (empty) checkout, so the script's `git rev-parse --show-toplevel` finds
# THIS tree. Without it git walks up past the temporary directory and answers with
# whatever repository happens to contain it — and the script, correctly, refuses
# to publish anything for a root that is not FLIP's.
git init -q "${FAKE}"

# The synthetic tree. The real script and the real window.js generator go in — a
# stubbed generator would test nothing about what window.js ends up containing.
cp "${SCRIPT}" "${FAKE}/deploy/providers/AWS/scripts/deploy-ui.sh"
cp "${REPO_ROOT}/flip-ui/scripts/generate-window-js.sh" "${FAKE}/flip-ui/scripts/generate-window-js.sh"
printf '{}\n' >"${FAKE}/flip-ui/package.json"

cat >"${BIN}/terraform" <<'STUB'
#!/usr/bin/env bash
echo "terraform $*" >>"${LOG}"
if [ "${1:-}" = "output" ] && [ "${2:-}" = "-raw" ]; then
    case "${3:-}" in
        FlipUiBucketName) echo "${STUB_UI_BUCKET:-}" ;;
        CloudfrontDistributionId) echo "${STUB_DIST_ID:-}" ;;
        CognitoUserPoolId) echo "${STUB_POOL:-}" ;;
        CognitoAppClientId) echo "${STUB_CLIENT:-}" ;;
        *) echo "unknown output ${3:-}" >&2; exit 1 ;;
    esac
    exit 0
fi
echo "terraform: unexpected invocation: $*" >&2
exit 1
STUB

# Logs whether the credentials it was handed survived to this point: the CI job's
# identity IS these env vars, and nothing in this script may unset them.
cat >"${BIN}/aws" <<'STUB'
#!/usr/bin/env bash
echo "aws $* [creds=${AWS_ACCESS_KEY_ID:+present}${AWS_ACCESS_KEY_ID:-unset}]" >>"${LOG}"
exit 0
STUB

cat >"${BIN}/npm" <<'STUB'
#!/usr/bin/env bash
echo "npm $*" >>"${LOG}"
if [ "${1:-}" = "run" ]; then
    mkdir -p dist/js
fi
exit 0
STUB

# Stands in for `node -pe 'JSON.stringify(process.argv[1] || "")' -- VALUE`.
cat >"${BIN}/node" <<'STUB'
#!/usr/bin/env bash
echo "node $*" >>"${LOG}"
value=""
while [ $# -gt 0 ]; do
    if [ "$1" = "--" ]; then value="${2:-}"; break; fi
    shift
done
printf '%s\n' "$(printf '%s' "${value}" | jq -Rr @json)"
STUB

# Only ever asked one question: is this a platform-managed estate?
cat >"${BIN}/make" <<'STUB'
#!/usr/bin/env bash
echo "make $*" >>"${LOG}"
echo "TF_VAR_environment=stag"
echo "TF_VAR_lza_managed_network=${STUB_LZA:-false}"
STUB

chmod +x "${BIN}"/*
export PATH="${BIN}:${PATH}"
export LOG

UI_DIR="${FAKE}/flip-ui"
ENV_FILE="${TEST_ROOT}/.env.stub"

# A complete window.js value set, mirroring an operator's .env.stag.
write_env_file() {
    cat >"${ENV_FILE}" <<EOF
AWS_REGION=eu-west-2
CENTRAL_HUB_API_URL=https://hub.example.com/api
AWS_COGNITO_USER_POOL_ID=eu-west-2_POOLFROMFILE
AWS_COGNITO_APP_CLIENT_ID=clientfromfile
BLACKLISTED_MODEL_FILES=flip.py
RELEASE_VERSION=v9.9.9
$1
EOF
}

# Runs the script the way a caller does: credentials in the environment, the env
# file by path, one mode token. The STUB_* reads use `-` rather than `:-` so a
# case can hand the script an *empty* output on purpose (the bucket, below).
run_deploy() {
    local mode="$1"
    : >"${LOG}"
    rm -rf "${UI_DIR}/dist"
    AWS_ACCESS_KEY_ID=stubkey AWS_SECRET_ACCESS_KEY=stubsecret AWS_SESSION_TOKEN=stubtoken \
        STUB_UI_BUCKET="${STUB_UI_BUCKET-flip-ui-bucket-from-state}" \
        STUB_DIST_ID="${STUB_DIST_ID-EDFDISTIDFROMSTATE}" \
        STUB_POOL="${STUB_POOL-eu-west-2_POOLFROMSTATE}" \
        STUB_CLIENT="${STUB_CLIENT-clientfromstate}" \
        STUB_LZA="${STUB_LZA-false}" \
        bash "${FAKE}/deploy/providers/AWS/scripts/deploy-ui.sh" \
        --env-file "${ENV_FILE}" --mode "${mode}" >"${TEST_ROOT}/out" 2>"${TEST_ROOT}/err"
    RC=$?
    OUT="$(cat "${TEST_ROOT}/out")"
    ERR="$(cat "${TEST_ROOT}/err")"
}

echo ""
echo "-- the legacy path: build, sync, invalidate"

export STUB_LZA=false
write_env_file ""
run_deploy stag

if [[ "${RC}" -eq 0 ]]; then
    ok "exits 0 (${OUT##*$'\n'})"
else
    no "exits 0" "rc=${RC}" "${ERR}" "${OUT}"
fi

# Every environment-specific value is read, not named: the script must contain no
# bucket or distribution id, and must ask Terraform for both.
if grep -q "terraform output -raw FlipUiBucketName" "${LOG}"; then
    ok "reads FlipUiBucketName from terraform output"
else
    no "reads FlipUiBucketName from terraform output" "$(grep '^terraform' "${LOG}")"
fi

if grep -q "terraform output -raw CloudfrontDistributionId" "${LOG}"; then
    ok "reads CloudfrontDistributionId from terraform output"
else
    no "reads CloudfrontDistributionId from terraform output" "$(grep '^terraform' "${LOG}")"
fi

# The hashed bundles are content-addressed; index.html and window.js reference
# them and may not be cached. One --cache-control for the sync, one per file.
if grep -q 'aws s3 sync .*s3://flip-ui-bucket-from-state/ .*--delete .*--cache-control public, max-age=31536000, immutable .*--exclude index.html --exclude js/window.js --exclude ark_demo/\*' "${LOG}"; then
    ok "syncs the bundle immutable, with the cache-control split and the ark_demo exclusion"
else
    no "syncs the bundle immutable, with the cache-control split and the ark_demo exclusion" \
        "$(grep '^aws s3 sync' "${LOG}")"
fi

if [ "$(grep -c 'aws s3 cp .*--cache-control no-cache, no-store, must-revalidate' "${LOG}")" -eq 2 ]; then
    ok "copies index.html and js/window.js as no-cache (2 copies)"
else
    no "copies index.html and js/window.js as no-cache (2 copies)" "$(grep '^aws s3 cp' "${LOG}")"
fi

if grep -q 'aws s3 cp .*dist/index.html s3://flip-ui-bucket-from-state/index.html --cache-control no-cache, no-store, must-revalidate --content-type text/html; charset=utf-8' "${LOG}"; then
    ok "index.html keeps its content-type"
else
    no "index.html keeps its content-type" "$(grep '^aws s3 cp' "${LOG}")"
fi

if grep -q 'aws s3 cp .*dist/js/window.js s3://flip-ui-bucket-from-state/js/window.js --cache-control no-cache, no-store, must-revalidate --content-type application/javascript; charset=utf-8' "${LOG}"; then
    ok "js/window.js keeps its content-type"
else
    no "js/window.js keeps its content-type" "$(grep '^aws s3 cp' "${LOG}")"
fi

if grep -q 'aws cloudfront create-invalidation --distribution-id EDFDISTIDFROMSTATE --paths /\*' "${LOG}"; then
    ok "invalidates the distribution read from state"
else
    no "invalidates the distribution read from state" "$(grep -E '^aws cloudfront' "${LOG}")"
fi

if grep -q 'npm ci' "${LOG}" && grep -q 'npm run build:deploy' "${LOG}"; then
    ok "builds with npm ci && npm run build:deploy (not the type-checked build)"
else
    no "builds with npm ci && npm run build:deploy" "$(grep '^npm' "${LOG}")"
fi

if grep -q 'window.AWS_USER_POOL_ID *= *"eu-west-2_POOLFROMFILE"' "${UI_DIR}/dist/js/window.js"; then
    ok "window.js is generated from the env file the caller passed"
else
    no "window.js is generated from the env file the caller passed" "$(cat "${UI_DIR}/dist/js/window.js" 2>/dev/null)"
fi

if grep -q 'aws s3 sync .*creds=present' "${LOG}"; then
    ok "leaves the credentials it was handed alone (CI's identity is those env vars)"
else
    no "leaves the credentials it was handed alone" "$(grep '^aws' "${LOG}" | head -3)"
fi

echo ""
echo "-- window.js's Cognito values fall back to the Terraform outputs"

# An environment that carries only the hub URL — the LZA-first case, and the
# reason CI needs no copy of the pool id: the state already has it.
write_env_file ""
grep -v '^AWS_COGNITO_' "${ENV_FILE}" >"${ENV_FILE}.tmp" && mv "${ENV_FILE}.tmp" "${ENV_FILE}"
run_deploy stag

if [[ "${RC}" -eq 0 ]] &&
    grep -q "terraform output -raw CognitoUserPoolId" "${LOG}" &&
    grep -q "terraform output -raw CognitoAppClientId" "${LOG}" &&
    grep -q 'window.AWS_USER_POOL_ID *= *"eu-west-2_POOLFROMSTATE"' "${UI_DIR}/dist/js/window.js"; then
    ok "reads both Cognito ids from state and emits them into window.js"
else
    no "reads both Cognito ids from state and emits them into window.js" \
        "rc=${RC}" "$(grep '^terraform' "${LOG}")" "$(cat "${UI_DIR}/dist/js/window.js" 2>/dev/null)"
fi

echo ""
echo "-- the LZA mode: no distribution, so no invalidation"

export STUB_LZA=true
write_env_file ""
run_deploy lza-stag

if [[ "${RC}" -eq 0 ]]; then
    ok "exits 0"
else
    no "exits 0" "rc=${RC}" "${ERR}" "${OUT}"
fi

if grep -q 'aws s3 sync ' "${LOG}" && ! grep -q 'aws cloudfront create-invalidation' "${LOG}"; then
    ok "still publishes, and never calls create-invalidation"
else
    no "still publishes, and never calls create-invalidation" "$(grep -E '^aws (s3 sync|cloudfront)' "${LOG}")"
fi

if ! grep -q 'terraform output -raw CloudfrontDistributionId' "${LOG}"; then
    ok "does not even ask for a distribution id"
else
    no "does not even ask for a distribution id" "$(grep '^terraform' "${LOG}")"
fi

echo ""
echo "-- fail closed"

export STUB_LZA=false
write_env_file ""

STUB_UI_BUCKET="" run_deploy stag
if [[ "${RC}" -ne 0 ]] && [[ "${ERR}" == *"FlipUiBucketName is empty"* ]]; then
    ok "an empty bucket output stops the run before anything is synced"
else
    no "an empty bucket output stops the run" "rc=${RC}" "${ERR}"
fi
STUB_UI_BUCKET="flip-ui-bucket-from-state"

if ! grep -q 'aws s3 sync' "${LOG}"; then
    ok "and nothing was synced after it"
else
    no "and nothing was synced after it" "$(grep '^aws' "${LOG}")"
fi

: >"${LOG}"
bash "${FAKE}/deploy/providers/AWS/scripts/deploy-ui.sh" --env-file "${TEST_ROOT}/nope.env" \
    >"${TEST_ROOT}/out" 2>"${TEST_ROOT}/err" && RC=0 || RC=$?
if [[ "${RC}" -ne 0 ]] && grep -q 'not found' "${TEST_ROOT}/err"; then
    ok "a missing env file stops the run"
else
    no "a missing env file stops the run" "rc=${RC}" "$(cat "${TEST_ROOT}/err")"
fi

bash "${FAKE}/deploy/providers/AWS/scripts/deploy-ui.sh" --nonsense \
    >"${TEST_ROOT}/out" 2>"${TEST_ROOT}/err" && RC=0 || RC=$?
if [[ "${RC}" -ne 0 ]] && grep -q 'unknown argument' "${TEST_ROOT}/err"; then
    ok "an unknown argument stops the run"
else
    no "an unknown argument stops the run" "rc=${RC}" "$(cat "${TEST_ROOT}/err")"
fi

bash "${FAKE}/deploy/providers/AWS/scripts/deploy-ui.sh" --help >"${TEST_ROOT}/out" 2>&1 && RC=0 || RC=$?
if [[ "${RC}" -eq 0 ]] && grep -q -- '--env-file' "${TEST_ROOT}/out"; then
    ok "--help exits 0 and documents the flags"
else
    no "--help exits 0 and documents the flags" "rc=${RC}" "$(cat "${TEST_ROOT}/out")"
fi

echo ""
echo "-- one implementation of the recipe"

# The laptop path must call the same script CI calls. If `make deploy-ui` grew its
# own copy of the sync/invalidate commands, the two would drift and the drift
# would only show up as a wrong cache header in production.
if grep -q 'scripts/deploy-ui.sh' "${MAKEFILE}"; then
    ok "make deploy-ui delegates to scripts/deploy-ui.sh"
else
    no "make deploy-ui delegates to scripts/deploy-ui.sh" "$(grep -n -A3 '^deploy-ui:' "${MAKEFILE}")"
fi

if ! sed -n '/^deploy-ui:/,/^\.PHONY/p' "${MAKEFILE}" | grep -qE 'aws (s3 sync|s3 cp|cloudfront create-invalidation)'; then
    ok "and no longer carries a second copy of the sync/invalidate commands"
else
    no "and no longer carries a second copy of the sync/invalidate commands" \
        "$(sed -n '/^deploy-ui:/,/^\.PHONY/p' "${MAKEFILE}" | grep -E 'aws ')"
fi

# The credentials the laptop path *does* control: $(_AWS_ENV) drops static keys so
# an operator's profile wins. It has to survive the delegation.
if sed -n '/^deploy-ui:/,/^\.PHONY/p' "${MAKEFILE}" | grep -q '_AWS_ENV'; then
    ok "make deploy-ui still runs under \$(_AWS_ENV), so a profile beats ambient keys"
else
    no "make deploy-ui still runs under \$(_AWS_ENV)" "$(sed -n '/^deploy-ui:/,/^\.PHONY/p' "${MAKEFILE}")"
fi

echo ""
echo "==== ${PASS} passed, ${FAIL} failed ===="
[[ "${FAIL}" -eq 0 ]]
