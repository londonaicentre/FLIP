#!/usr/bin/env bash
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
# Publish the flip-ui bundle: build it, generate window.js from the environment,
# sync to the UI bucket with the cache-control split, invalidate the distribution.
#
# This is the single implementation of that recipe, and there are two callers:
#
#   * `make deploy-ui` (deploy/providers/AWS/Makefile), from a laptop. It runs
#     this script under `$(_AWS_ENV)`, so an ambient AWS_ACCESS_KEY_ID cannot
#     take priority over the profile the operator chose.
#   * `.github/workflows/deploy_ui.yml`, on merge. There the credentials ARE the
#     environment (OIDC), so this script must never unset or export them — the
#     callers deal with identity, this file deals with the bundle.
#
# Everything environment-specific is read, never hard-coded: the bucket and the
# distribution id come from `terraform output` (so a renamed bucket, or the LZA
# move, needs no change here), and the window.js values come from the env file
# that `scripts/compose-ci-env.sh` composes on CI and that the operator keeps by
# hand on a laptop — the same file in both cases, so the two paths cannot drift.
#
#   deploy-ui.sh [--env-file PATH] [--mode TOKEN]
#
#     --env-file PATH   .env.<env> to read window.js's values from. The Makefile
#                       passes $(MAIN_ENV_FILE), the workflow its composed file.
#                       Without it the values must already be in the environment.
#     --mode TOKEN      deploy/env_mode.mk token (stag | true | lza-stag | lza).
#                       Only used to ask the Makefile whether this is a
#                       platform-managed estate, which decides the invalidation
#                       step below. Defaults to stag.
set -euo pipefail

die() {
    echo "❌ $*" >&2
    exit 1
}

ENV_FILE=""
MODE="stag"
while [ $# -gt 0 ]; do
    case "$1" in
        --env-file) ENV_FILE="${2:-}"; shift 2 ;;
        --mode) MODE="${2:-}"; shift 2 ;;
        -h|--help)
            cat <<'USAGE'
deploy-ui.sh [--env-file PATH] [--mode TOKEN]

  --env-file PATH   .env.<env> holding window.js's values. `make deploy-ui`
                    passes $(MAIN_ENV_FILE); the workflow passes the file
                    scripts/compose-ci-env.sh composed. Without it those values
                    must already be in the environment.
  --mode TOKEN      deploy/env_mode.mk token (stag | true | lza-stag | lza).
                    Only asked of the Makefile to decide whether there is a
                    distribution to invalidate. Defaults to stag.
USAGE
            exit 0 ;;
        *) die "unknown argument '$1' (see --help)" ;;
    esac
done

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TF_ROOT="$(cd "${HERE}/.." && pwd)"

# Resolved through git where possible — correct for a worktree, or when the
# script is called by absolute path — and then checked rather than assumed.
REPO_ROOT="$(git -C "${HERE}" rev-parse --show-toplevel 2>/dev/null || true)"
[[ -n "${REPO_ROOT}" ]] || REPO_ROOT="$(cd "${HERE}/../../../.." && pwd)"
[[ -f "${REPO_ROOT}/flip-ui/package.json" ]] || die "cannot resolve the repository root from ${HERE} (tried '${REPO_ROOT}')"
UI_DIR="${REPO_ROOT}/flip-ui"

# The env file is read into the environment, the way the Makefile's
# `include` + `export $(shell sed …)` does: generate-window-js.sh reads
# AWS_COGNITO_USER_POOL_ID, AWS_COGNITO_APP_CLIENT_ID, CENTRAL_HUB_API_URL,
# AWS_REGION, BLACKLISTED_MODEL_FILES and RELEASE_VERSION from the shell.
if [[ -n "${ENV_FILE}" ]]; then
    [[ -f "${ENV_FILE}" ]] || die "env file '${ENV_FILE}' not found"
    ENV_FILE="$(cd "$(dirname "${ENV_FILE}")" && pwd)/$(basename "${ENV_FILE}")"
    set -a
    # shellcheck disable=SC1090  # a runtime path, and the file is the caller's own
    . "${ENV_FILE}"
    set +a
fi

command -v terraform >/dev/null || die "terraform not found on PATH — it supplies the bucket and distribution id"
command -v npm >/dev/null || die "npm not found on PATH — it builds the bundle"

tf_output() {
    ( cd "${TF_ROOT}" && terraform output -raw "$1" ) || die "terraform output $1 failed (is ${TF_ROOT} initialised?)"
}

# The mode decides one thing only: whether there is a distribution to invalidate.
# Asked of the Makefile that owns deploy/env_mode.mk rather than re-derived here,
# so a new PROD token is a one-place change.
tf_lza="$(make --no-print-directory -C "${TF_ROOT}" print-tf-env PROD="${MODE}" \
    | sed -n 's/^TF_VAR_lza_managed_network=//p')"
[[ -n "${tf_lza}" ]] || die "cannot read TF_VAR_lza_managed_network from 'make -C ${TF_ROOT} print-tf-env PROD=${MODE}'"

# window.js's Cognito values are deployment *outputs*: read them from the state
# when the environment does not carry them, so CI needs no copy of them.
for pair in "AWS_COGNITO_USER_POOL_ID:CognitoUserPoolId" "AWS_COGNITO_APP_CLIENT_ID:CognitoAppClientId"; do
    var="${pair%%:*}"; out="${pair##*:}"
    if [[ -z "$(printenv "${var}" || true)" ]]; then
        value="$(tf_output "${out}")"
        [[ -n "${value}" ]] || die "neither ${var} nor terraform output ${out} is set"
        export "${var}=${value}"
    fi
done

UI_BUCKET="$(tf_output FlipUiBucketName)"
[[ -n "${UI_BUCKET}" ]] || die "terraform output FlipUiBucketName is empty"
DIST_ID=""
if [[ "${tf_lza}" != "true" ]]; then
    DIST_ID="$(tf_output CloudfrontDistributionId)"
    [[ -n "${DIST_ID}" ]] || die "terraform output CloudfrontDistributionId is empty"
fi

echo "🖼️  Building flip-ui in ${UI_DIR} ..."
# Uses build:deploy (vite build only). The type-checked `npm run build` fails on
# pre-existing TypeScript errors in the repo — tracked separately.
( cd "${UI_DIR}" && npm ci && npm run build:deploy )
echo "🔧 Generating window.js from the environment..."
( cd "${UI_DIR}" && sh scripts/generate-window-js.sh > dist/js/window.js )

echo "📦 Syncing static assets to s3://${UI_BUCKET}/ ..."
# Hashed bundles are content-addressed and safe to cache forever.
# index.html and js/window.js must never be cached long — they reference the
# hashed bundles. ark_demo/* is excluded so --delete cannot remove the public
# Ark+ demo assets that live under the same distribution.
aws s3 sync "${UI_DIR}/dist/" "s3://${UI_BUCKET}/" \
    --delete \
    --cache-control "public, max-age=31536000, immutable" \
    --exclude "index.html" --exclude "js/window.js" --exclude "ark_demo/*"
aws s3 cp "${UI_DIR}/dist/index.html" "s3://${UI_BUCKET}/index.html" \
    --cache-control "no-cache, no-store, must-revalidate" \
    --content-type "text/html; charset=utf-8"
aws s3 cp "${UI_DIR}/dist/js/window.js" "s3://${UI_BUCKET}/js/window.js" \
    --cache-control "no-cache, no-store, must-revalidate" \
    --content-type "application/javascript; charset=utf-8"

# On LZA there is no workload distribution to invalidate (cloudfront.tf gates it
# off; the networking account's edge distribution serves the bucket) and no
# cross-account invalidation permission -- BY DESIGN (FLIP#749): the hashed
# bundles are immutable and index.html/window.js carry no-cache metadata, so a
# deploy is visible on the next index.html fetch with no invalidation at all.
if [[ "${tf_lza}" == "true" ]]; then
    echo "🧹 Skipping CloudFront invalidation on LZA (edge distribution honours the Cache-Control metadata)."
else
    echo "🧹 Invalidating CloudFront ${DIST_ID}..."
    aws cloudfront create-invalidation \
        --distribution-id "${DIST_ID}" \
        --paths "/*" >/dev/null
fi

echo "✅ deploy-ui complete."
