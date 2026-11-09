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

# Black-box tests for scripts/image-wait-budgets.sh: how long terraform_apply.yml
# waits for each image group (FLIP#1811).
#
# Drives the REAL script with `gh` stubbed on PATH. The stub answers each
# workflow's runs query from a fixture file, so no token and no network.
#
# The invariant: a group gets the full budget while its build may still publish
# the image, and on any doubt; 0 only when GitHub positively says no build is
# coming.
#
# Usage:
#     bash deploy/providers/AWS/scripts/tests/test_image_wait_budgets.sh

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$(cd "${HERE}/.." && pwd)/image-wait-budgets.sh"

TEST_ROOT="$(mktemp -d)"
trap 'rm -rf "${TEST_ROOT}"' EXIT

MOCKBIN="${TEST_ROOT}/mockbin"
mkdir -p "${MOCKBIN}"

# gh: `gh api <path>` prints ${FIXTURE_DIR}/<workflow file>.json, or
# {"workflow_runs":[]} when there is none. ${FIXTURE_DIR}/gh-exit makes every call
# fail with that code. Every path is logged to ${FIXTURE_DIR}/gh.log.
cat >"${MOCKBIN}/gh" <<'MOCK_GH'
#!/usr/bin/env bash
[[ "$1" == "api" ]] || exit 64
echo "$2" >>"${FIXTURE_DIR}/gh.log"
if [[ -s "${FIXTURE_DIR}/gh-exit" ]]; then
    echo "gh: HTTP 403: Resource not accessible by integration" >&2
    exit "$(cat "${FIXTURE_DIR}/gh-exit")"
fi
workflow="${2#*/actions/workflows/}"
workflow="${workflow%%/*}"
if [[ -f "${FIXTURE_DIR}/${workflow}.json" ]]; then
    cat "${FIXTURE_DIR}/${workflow}.json"
else
    echo '{"total_count":0,"workflow_runs":[]}'
fi
MOCK_GH
chmod +x "${MOCKBIN}/gh"

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

SHA="abc1234def5678901234567890123456789abcde" # pragma: allowlist secret

fixture() {
    FIXTURE_DIR="${TEST_ROOT}/fx-${RANDOM}${RANDOM}"
    mkdir -p "${FIXTURE_DIR}"
    : >"${FIXTURE_DIR}/gh.log"
    export FIXTURE_DIR
}

# runs <workflow file> <status>[:<conclusion>] ...: the runs that workflow has.
runs() {
    local workflow="$1" entries="" run status conclusion
    shift
    for run in "$@"; do
        status="${run%%:*}"
        conclusion="null"
        [[ "${run}" == *:* ]] && conclusion="\"${run#*:}\""
        entries="${entries:+${entries},}{\"status\":\"${status}\",\"conclusion\":${conclusion}}"
    done
    printf '{"total_count":%s,"workflow_runs":[%s]}\n' "$#" "${entries}" >"${FIXTURE_DIR}/${workflow}.json"
}

# Run the script; trailing KEY=VALUE arguments are added to its environment.
run_budgets() {
    local title="$1"
    shift
    echo ""
    echo "-- ${title}"
    STDOUT="$(env PATH="${MOCKBIN}:${PATH}" \
        GITHUB_REPOSITORY="londonaicentre/FLIP" \
        GITHUB_SHA="${SHA}" \
        FL_BACKEND="nvflare" \
        GHCR_WAIT_SECONDS=900 \
        RELEASE_TAG= \
        "$@" \
        bash "${SCRIPT}" 2>"${TEST_ROOT}/err")"
    RC=$?
    STDERR="$(cat "${TEST_ROOT}/err")"
}

expect() {
    local want="$1"
    if [[ "${RC}" -eq 0 && "${STDOUT}" == "${want}" ]]; then
        ok "$(printf '%s' "${want}" | tr '\n' ' ')"
    else
        no "$(printf '%s' "${want}" | tr '\n' ' ')" "exit ${RC}, got: $(printf '%s' "${STDOUT}" | tr '\n' ' ')" \
            "stderr: ${STDERR}"
    fi
}

FULL=$'hub=900\nfl=900'
NONE=$'hub=0\nfl=0'

echo "==== image-wait-budgets.sh ===="

# 1. A commit that builds nothing — the fl_quiesced re-dispatch of an infra-only
#    merge — probes once for both groups instead of waiting 2 x 900s.
fixture
run_budgets "no build ran for this commit"
expect "${NONE}"

# 2. Both builds still going: wait for both.
fixture
runs test_flip_api.yml in_progress
runs fl-docker-build-nvflare.yml queued
run_budgets "both builds running"
expect "${FULL}"

# 3. One group building waits for that group only.
fixture
runs test_flip_api.yml queued
run_budgets "only the hub build ran"
expect $'hub=900\nfl=0'

fixture
runs fl-docker-build-nvflare.yml completed:success
run_budgets "only the FL build ran"
expect $'hub=0\nfl=900'

# 4. A green run has published, or is about to (the hub image is built after its
#    tests pass), so it still gets the budget; the resolver stops at the image.
fixture
runs test_flip_api.yml completed:success
runs fl-docker-build-nvflare.yml completed:success
run_budgets "both builds green"
expect "${FULL}"

# 5. A finished run that is not green publishes nothing.
for conclusion in failure cancelled skipped timed_out startup_failure; do
    fixture
    runs test_flip_api.yml "completed:${conclusion}"
    runs fl-docker-build-nvflare.yml "completed:${conclusion}"
    run_budgets "builds concluded ${conclusion}"
    expect "${NONE}"
done

# 6. A re-run or a second push of the same sha: any live or green run counts.
fixture
runs test_flip_api.yml completed:failure in_progress
runs fl-docker-build-nvflare.yml completed:cancelled completed:success
run_budgets "a red run and a newer one"
expect "${FULL}"

# 7. Waiting on an environment approval is not finished.
fixture
runs test_flip_api.yml waiting
run_budgets "a run waiting for approval"
expect $'hub=900\nfl=0'

# 8. The FL group asks the estate's own backend: on a FLARE estate a Flower build
#    is not this estate's image.
fixture
runs fl-docker-build-flower.yml in_progress
run_budgets "nvflare ignores a flower build"
expect "${NONE}"
run_budgets "flower waits for its build" FL_BACKEND=flower
expect $'hub=0\nfl=900'

# 9. The query: this commit's push runs of each workflow, in this repository.
fixture
run_budgets "the runs queried"
want="repos/londonaicentre/FLIP/actions/workflows/test_flip_api.yml/runs?head_sha=${SHA}&event=push
repos/londonaicentre/FLIP/actions/workflows/fl-docker-build-nvflare.yml/runs?head_sha=${SHA}&event=push"
if [[ "$(cat "${FIXTURE_DIR}/gh.log")" == "${want}" ]]; then
    ok "asks for head_sha=<sha>&event=push of both workflows"
else
    no "asks for head_sha=<sha>&event=push of both workflows" "got: $(cat "${FIXTURE_DIR}/gh.log")"
fi

# 10. FAIL SAFE. An API error (a missing actions: read, an outage) must never read
#     as "no build": that would deploy the running image over a fresh build.
fixture
printf '1' >"${FIXTURE_DIR}/gh-exit"
run_budgets "an API error waits the full budget"
expect "${FULL}"
if [[ "${STDERR}" == *"could not read"* ]]; then
    ok "says it could not read the runs"
else
    no "says it could not read the runs" "stderr: ${STDERR}"
fi

for body in 'not json' '{"message":"Not Found"}' '{"workflow_runs":null}' ''; do
    fixture
    printf '%s' "${body}" >"${FIXTURE_DIR}/test_flip_api.yml.json"
    printf '%s' "${body}" >"${FIXTURE_DIR}/fl-docker-build-nvflare.yml.json"
    run_budgets "an unexpected response ('${body}') waits the full budget"
    expect "${FULL}"
done

# 11. RELEASE PATH. A release apply's images come from builds dispatched at the
#     tag, never from push runs at this sha, so "no push run" must not cut its
#     budget. Behaviour is unchanged from before FLIP#1811, and gh is not asked.
fixture
run_budgets "a release apply keeps the full budget" RELEASE_TAG=v1.2.3
expect "${FULL}"
if [[ ! -s "${FIXTURE_DIR}/gh.log" ]]; then
    ok "does not query workflow runs for a release"
else
    no "does not query workflow runs for a release" "gh was called: $(cat "${FIXTURE_DIR}/gh.log")"
fi

# 12. The budget is GHCR_WAIT_SECONDS, default 900.
fixture
runs test_flip_api.yml in_progress
run_budgets "a configured budget" GHCR_WAIT_SECONDS=300
expect $'hub=300\nfl=0'
run_budgets "the default budget" GHCR_WAIT_SECONDS=
expect $'hub=900\nfl=0'

# 13. Bad inputs stop the step rather than guess.
for bad in "FL_BACKEND=jax" "GHCR_WAIT_SECONDS=15m" "GITHUB_SHA=" "GITHUB_REPOSITORY="; do
    fixture
    run_budgets "rejects ${bad}" "${bad}"
    if [[ "${RC}" -ne 0 && "${STDERR}" == *"${bad%%=*}"* && -z "${STDOUT}" ]]; then
        ok "rejects ${bad}"
    else
        no "rejects ${bad}" "exit ${RC}, stdout: ${STDOUT}, stderr: ${STDERR}"
    fi
done

echo ""
echo "==== ${PASS} passed, ${FAIL} failed ===="
[[ "${FAIL}" -eq 0 ]]
