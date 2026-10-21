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

# Black-box tests for scripts/publish-ui.sh, which `make deploy-ui` and .github/workflows/publish_ui.yml
# both run to put a flip-ui build in its bucket (FLIP#1186).
#
# Drives the REAL script with `aws` stubbed on PATH (no credentials, no network). The stub records every
# call, so a test can check the cache-control split, the ark_demo/ exclusion and the invalidation.
#
# The invariants under test: hashed bundles are cached for a year and index.html / js/window.js never;
# --delete never reaches ark_demo/; the entry points are written after the bundles they reference; the
# invalidation runs only when a distribution is given; a build missing its entry points publishes nothing.
#
# Usage:
#     bash deploy/providers/AWS/scripts/tests/test_publish_ui.sh

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$(cd "${HERE}/.." && pwd)/publish-ui.sh"

TEST_ROOT="$(mktemp -d)"
trap 'rm -rf "${TEST_ROOT}"' EXIT

MOCKBIN="${TEST_ROOT}/mockbin"
mkdir -p "${MOCKBIN}"

# aws: every call is appended to ${FIXTURE_DIR}/calls, one per line.
#   ${FIXTURE_DIR}/fail-<service>-<command> — present: that call fails the way an API error does
cat >"${MOCKBIN}/aws" <<'MOCK_AWS'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"${FIXTURE_DIR}/calls"
if [[ -e "${FIXTURE_DIR}/fail-$1-$2" ]]; then
    echo "An error occurred (AccessDenied) when calling $1 $2" >&2
    exit 1
fi
case "$1 $2" in
    "s3 sync" | "s3 cp" | "cloudfront create-invalidation") ;;
    *) echo "unexpected aws call: $*" >&2; exit 1 ;;
esac
MOCK_AWS
chmod +x "${MOCKBIN}/aws"

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

# A fixture holding a complete build: entry points plus one hashed bundle.
fixture() {
    FIXTURE_DIR="${TEST_ROOT}/fx-${RANDOM}${RANDOM}"
    DIST="${FIXTURE_DIR}/dist"
    mkdir -p "${DIST}/js" "${DIST}/assets"
    printf '<!doctype html>\n' >"${DIST}/index.html"
    printf 'window.AWS_BASE_URL = "https://hub.example/api";\n' >"${DIST}/js/window.js"
    printf 'console.log(1)\n' >"${DIST}/assets/index-abc123.js"
    : >"${FIXTURE_DIR}/calls"
    export FIXTURE_DIR
}

run_publish() {
    echo ""
    echo "-- $1"
    shift
    STDOUT="$(env PATH="${MOCKBIN}:${PATH}" bash "${SCRIPT}" "$@" 2>"${TEST_ROOT}/err")"
    RC=$?
    STDERR="$(cat "${TEST_ROOT}/err")"
    CALLS="$(cat "${FIXTURE_DIR}/calls")"
}

expect_rc() {
    if [[ "${RC}" -eq "$1" ]]; then
        ok "$2"
    else
        no "$2" "wanted exit $1, got ${RC}" "stdout: ${STDOUT}" "stderr: ${STDERR}"
    fi
}

expect_call() {
    if printf '%s\n' "${CALLS}" | grep -Fq -- "$1"; then
        ok "$2"
    else
        no "$2" "no aws call containing: $1" "calls: ${CALLS}"
    fi
}

expect_no_call() {
    if printf '%s\n' "${CALLS}" | grep -Fq -- "$1"; then
        no "$2" "unexpected aws call containing: $1" "calls: ${CALLS}"
    else
        ok "$2"
    fi
}

# The line number of the first call containing $1, or 0.
call_line() {
    printf '%s\n' "${CALLS}" | grep -nF -- "$1" | head -1 | cut -d: -f1
}

echo "==== publish-ui.sh ===="

# 1. THE LZA CASE. No distribution: sync, the two entry points, no invalidation.
fixture
run_publish "a complete build, no distribution" "${DIST}" flip-ui-bucket
expect_rc 0 "succeeds"
expect_call "s3 sync ${DIST}/ s3://flip-ui-bucket/ --delete --cache-control public, max-age=31536000, immutable" \
    "syncs the bundles with --delete and a one-year immutable cache"
expect_call '--exclude index.html --exclude js/window.js --exclude ark_demo/*' \
    "keeps the entry points and the Ark+ demo out of the sync"
expect_call "s3 cp ${DIST}/index.html s3://flip-ui-bucket/index.html --cache-control no-cache, no-store, must-revalidate --content-type text/html; charset=utf-8" \
    "writes index.html uncached, as HTML"
expect_call "s3 cp ${DIST}/js/window.js s3://flip-ui-bucket/js/window.js --cache-control no-cache, no-store, must-revalidate --content-type application/javascript; charset=utf-8" \
    "writes js/window.js uncached, as JavaScript"
expect_no_call "cloudfront" "invalidates nothing"
sync_at="$(call_line "s3 sync")"
index_at="$(call_line "index.html s3://")"
if [[ -n "${sync_at}" && -n "${index_at}" && "${sync_at}" -lt "${index_at}" ]]; then
    ok "writes index.html after the bundles it references"
else
    no "writes index.html after the bundles it references" "calls: ${CALLS}"
fi

# 2. A self-owned account: the FLIP root's distribution is invalidated after the upload.
fixture
run_publish "a complete build and a distribution id" "${DIST}" flip-ui-bucket E2ABCDEF123456
expect_rc 0 "succeeds"
expect_call "cloudfront create-invalidation --distribution-id E2ABCDEF123456 --paths /*" \
    "invalidates every path on that distribution"

# 3. An empty distribution id is the LZA case, as `make deploy-ui` passes it.
fixture
run_publish "an empty distribution id" "${DIST}" flip-ui-bucket ""
expect_rc 0 "succeeds"
expect_no_call "cloudfront" "invalidates nothing"

# 4. A build without its entry points would leave the bucket serving the previous index.html, or none.
fixture
rm "${DIST}/index.html"
run_publish "the build has no index.html" "${DIST}" flip-ui-bucket
expect_rc 1 "fails"
expect_no_call "s3" "publishes nothing"

fixture
rm "${DIST}/js/window.js"
run_publish "the build has no js/window.js" "${DIST}" flip-ui-bucket
expect_rc 1 "fails"
expect_no_call "s3" "publishes nothing"

# 5. A failed sync stops before the entry points move to bundles that may not be there.
fixture
: >"${FIXTURE_DIR}/fail-s3-sync"
run_publish "the sync fails" "${DIST}" flip-ui-bucket E2ABCDEF123456
expect_rc 1 "fails"
expect_no_call "s3 cp" "writes no entry point"
expect_no_call "cloudfront" "invalidates nothing"

# 6. Both positional arguments are required.
fixture
run_publish "no bucket" "${DIST}"
if [[ "${RC}" -ne 0 ]]; then ok "fails"; else no "fails" "exited 0"; fi
expect_no_call "s3" "publishes nothing"

echo ""
echo "==== ${PASS} passed, ${FAIL} failed ===="
[[ "${FAIL}" -eq 0 ]]
