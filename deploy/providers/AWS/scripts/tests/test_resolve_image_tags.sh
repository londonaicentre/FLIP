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

# Black-box tests for scripts/resolve-image-tags.sh — which tag an automated
# apply bakes into the ECS task definitions.
#
# Drives the REAL script with `docker` and `aws` stubbed on PATH (no registry, no
# credentials, no network). The registry stub answers from a fixture list of
# published references; the ECS stub answers from a fixture of live images.
#
# The invariant under test is narrow and important: the script may fall back to
# the *configured* tag ONLY when there is no running service to read a tag from.
# Substituting it while a service runs would silently un-pin the released image
# (FLIP#751) while looking like a successful deploy. Note this is about
# substitution, not about the string — an environment genuinely running `:stag`
# gets `:stag` back from step 2, which is a no-op and correct.
#
# Usage:
#     bash deploy/providers/AWS/scripts/tests/test_resolve_image_tags.sh

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$(cd "${HERE}/.." && pwd)/resolve-image-tags.sh"

TEST_ROOT="$(mktemp -d)"
trap 'rm -rf "${TEST_ROOT}"' EXIT

MOCKBIN="${TEST_ROOT}/mockbin"
mkdir -p "${MOCKBIN}"

# docker: `manifest inspect <ref>` succeeds when <ref> is listed in
# ${FIXTURE_DIR}/published, one reference per line.
cat >"${MOCKBIN}/docker" <<'MOCK_DOCKER'
#!/usr/bin/env bash
if [[ "$1" == "manifest" && "$2" == "inspect" ]]; then
    grep -Fxq "$3" "${FIXTURE_DIR}/published" 2>/dev/null || exit 1
    echo '{"schemaVersion":2}'
    exit 0
fi
exit 1
MOCK_DOCKER

# aws: enough of `ecs describe-services` / `ecs describe-task-definition` to
# report what a service is running.
#   ${FIXTURE_DIR}/<service>.taskdef  — task definition ARN ("" ⇒ no service)
#   ${FIXTURE_DIR}/<service>.image    — image on the matching container
cat >"${MOCKBIN}/aws" <<'MOCK_AWS'
#!/usr/bin/env bash
service="" taskdef="" query=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --services) service="$2"; shift ;;
        --task-definition) taskdef="$2"; shift ;;
        --query) query="$2"; shift ;;
    esac
    shift
done
if [[ -n "${service}" ]]; then
    fx="${FIXTURE_DIR}/${service}.taskdef"
    [[ -s "${fx}" ]] || { echo "None"; exit 0; }
    cat "${fx}"
    exit 0
fi
if [[ -n "${taskdef}" ]]; then
    # taskdef fixture files hold "<service>" so the image fixture can be found.
    svc="${taskdef%%:*}"
    fx="${FIXTURE_DIR}/${svc}.image"
    [[ -s "${fx}" ]] || { echo "None"; exit 0; }
    cat "${fx}"
    exit 0
fi
exit 1
MOCK_AWS

chmod +x "${MOCKBIN}/docker" "${MOCKBIN}/aws"

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

# A fabricated commit sha — the resolver only ever takes its first 7 characters.
GIT_SHA="abc1234def5678901234567890123456789abcde"  # pragma: allowlist secret
SHA_TAG="sha-abc1234"

# Set up a fixture directory. Args:
#   $1  newline-separated published image references
#   $2  flip-api task-definition ARN ("" ⇒ service absent)
#   $3  flip-api live image
#   $4  fl-server-net-1 task-definition ARN
#   $5  fl-server-net-1 live image
fixture() {
    FIXTURE_DIR="${TEST_ROOT}/fx-${RANDOM}"
    mkdir -p "${FIXTURE_DIR}"
    printf '%s\n' "$1" >"${FIXTURE_DIR}/published"
    printf '%s' "$2" >"${FIXTURE_DIR}/flip-api.taskdef"
    printf '%s' "$3" >"${FIXTURE_DIR}/flip-api.image"
    printf '%s' "$4" >"${FIXTURE_DIR}/fl-server-net-1.taskdef"
    printf '%s' "$5" >"${FIXTURE_DIR}/fl-server-net-1.image"
    export FIXTURE_DIR
}

run_resolve() {
    local title="$1"
    shift
    echo ""
    echo "-- ${title}"
    STDOUT="$(PATH="${MOCKBIN}:${PATH}" \
        GIT_SHA="${GIT_SHA}" \
        DOCKER_REGISTRY="ghcr.io/londonaicentre/" \
        FALLBACK_DOCKER_TAG="stag" \
        FALLBACK_DOCKER_FL_TAG="stag" \
        FL_BACKEND="${FL_BACKEND_UNDER_TEST:-nvflare}" \
        GHCR_WAIT_SECONDS=0 \
        GHCR_POLL_SECONDS=0 \
        "$@" \
        bash "${SCRIPT}" 2>"${TEST_ROOT}/err")"
    RC=$?
    STDERR="$(cat "${TEST_ROOT}/err")"
}

expect_tag() {
    local key="$1" want="$2"
    local got
    got="$(printf '%s\n' "${STDOUT}" | sed -n "s/^${key}=//p")"
    if [[ "${got}" == "${want}" ]]; then
        ok "${key}=${want}"
    else
        no "${key}=${want}" "got: ${key}=${got}" "stderr: ${STDERR}"
    fi
}

echo "==== resolve-image-tags.sh ===="

# 1. THE RELEASE CASE. Both images published for this commit — pin the sha tag,
#    which is what makes a merge to main a recorded, rollback-able release.
fixture "ghcr.io/londonaicentre/flip-api:${SHA_TAG}
ghcr.io/londonaicentre/flare-fl-server:${SHA_TAG}" \
    "flip-api:41" "ghcr.io/londonaicentre/flip-api:sha-9999999" \
    "fl-server-net-1:12" "ghcr.io/londonaicentre/flare-fl-server:sha-9999999"
run_resolve "both images published for this commit"
expect_tag DOCKER_TAG "${SHA_TAG}"
expect_tag DOCKER_FL_TAG "${SHA_TAG}"

# 2. THE COMMON CASE. An infrastructure-only merge publishes no image, so keep
#    running exactly what is running. Falling back to `:stag` here is the bug
#    this script exists to prevent — it would mint a task definition pointing at
#    a mutable tag and quietly discard the pin.
fixture "" \
    "flip-api:41" "ghcr.io/londonaicentre/flip-api:sha-9999999" \
    "fl-server-net-1:12" "ghcr.io/londonaicentre/flare-fl-server:sha-8888888"
run_resolve "no image published — reuse the live tags"
expect_tag DOCKER_TAG "sha-9999999"
expect_tag DOCKER_FL_TAG "sha-8888888"
if [[ "${STDOUT}" == *"=stag"* ]]; then
    no "never substitutes the configured tag while a service is running" \
        "resolved to the configured tag, un-pinning the release"
else
    ok "never substitutes the configured tag while a service is running"
fi

# 3. PARTIAL PUBLISH. A merge that changed only flip-api pins the hub image and
#    leaves FL where it is — the two are resolved independently on purpose.
fixture "ghcr.io/londonaicentre/flip-api:${SHA_TAG}" \
    "flip-api:41" "ghcr.io/londonaicentre/flip-api:sha-9999999" \
    "fl-server-net-1:12" "ghcr.io/londonaicentre/flare-fl-server:sha-8888888"
run_resolve "only the hub image published"
expect_tag DOCKER_TAG "${SHA_TAG}"
expect_tag DOCKER_FL_TAG "sha-8888888"

# 4. FIRST APPLY into an empty account: no service exists, so there is no live
#    tag to reuse and the configured tag is the only remaining answer.
fixture "" "" "" "" ""
run_resolve "no service yet — configured tag is the only option"
expect_tag DOCKER_TAG "stag"
expect_tag DOCKER_FL_TAG "stag"

# 5. Only one service missing — the other still reuses its live tag.
fixture "" \
    "" "" \
    "fl-server-net-1:12" "ghcr.io/londonaicentre/flare-fl-server:sha-8888888"
run_resolve "hub service absent, FL service running"
expect_tag DOCKER_TAG "stag"
expect_tag DOCKER_FL_TAG "sha-8888888"

# 6. A digest-pinned live image has no tag to reuse. Reporting the digest
#    fragment as a tag would produce an unpullable reference, so it must fall
#    through rather than invent one.
fixture "" \
    "flip-api:41" "ghcr.io/londonaicentre/flip-api@sha256:0123456789abcdef" \
    "fl-server-net-1:12" "ghcr.io/londonaicentre/flare-fl-server:sha-8888888"
run_resolve "digest-pinned live image falls through"
expect_tag DOCKER_TAG "stag"

# 7. The FL image name follows FL_BACKEND — probing the wrong repository would
#    make every Flower deployment silently take the fallback path.
fixture "ghcr.io/londonaicentre/flower-superlink:${SHA_TAG}" \
    "flip-api:41" "ghcr.io/londonaicentre/flip-api:sha-9999999" \
    "fl-server-net-1:12" "ghcr.io/londonaicentre/flower-superlink:sha-8888888"
FL_BACKEND_UNDER_TEST=flower run_resolve "flower resolves flower-superlink"
expect_tag DOCKER_FL_TAG "${SHA_TAG}"

# The same fixture under nvflare must NOT match — proof the probe is
# backend-specific rather than matching any published tag.
fixture "ghcr.io/londonaicentre/flower-superlink:${SHA_TAG}" \
    "flip-api:41" "ghcr.io/londonaicentre/flip-api:sha-9999999" \
    "fl-server-net-1:12" "ghcr.io/londonaicentre/flare-fl-server:sha-8888888"
run_resolve "nvflare does not match a flower publish"
expect_tag DOCKER_FL_TAG "sha-8888888"

# 8. Required inputs are asserted rather than defaulted — an empty
#    DOCKER_REGISTRY would probe `flip-api:sha-…` on Docker Hub.
echo ""
echo "-- required inputs are asserted"
for missing in GIT_SHA DOCKER_REGISTRY FALLBACK_DOCKER_TAG FALLBACK_DOCKER_FL_TAG FL_BACKEND; do
    # Build the env list omitting one key. Note `env -u X X=v` re-adds X, so the
    # omission has to happen when assembling the list, not with -u.
    declare -a envs=()
    for pair in "GIT_SHA=${GIT_SHA}" "DOCKER_REGISTRY=ghcr.io/x/" \
        "FALLBACK_DOCKER_TAG=stag" "FALLBACK_DOCKER_FL_TAG=stag" "FL_BACKEND=nvflare"; do
        [[ "${pair%%=*}" == "${missing}" ]] || envs+=("${pair}")
    done
    out="$(env -i PATH="${MOCKBIN}:${PATH}" FIXTURE_DIR="${FIXTURE_DIR}" \
        GHCR_WAIT_SECONDS=0 GHCR_POLL_SECONDS=0 "${envs[@]}" \
        bash "${SCRIPT}" 2>&1 >/dev/null)"
    rc=$?
    if [[ "${rc}" -ne 0 && "${out}" == *"${missing}"* ]]; then
        ok "missing ${missing} fails with a named error"
    else
        no "missing ${missing} fails with a named error" "exit ${rc}: ${out}"
    fi
done

# 9. An unknown backend must stop rather than guess an image name.
echo ""
echo "-- unknown FL_BACKEND"
out="$(PATH="${MOCKBIN}:${PATH}" GIT_SHA="${GIT_SHA}" DOCKER_REGISTRY="ghcr.io/x/" \
    FALLBACK_DOCKER_TAG=stag FALLBACK_DOCKER_FL_TAG=stag FL_BACKEND=jax \
    GHCR_WAIT_SECONDS=0 GHCR_POLL_SECONDS=0 bash "${SCRIPT}" 2>&1 >/dev/null)"
rc=$?
if [[ "${rc}" -ne 0 && "${out}" == *"nvflare"* ]]; then
    ok "rejects an unknown backend"
else
    no "rejects an unknown backend" "exit ${rc}: ${out}"
fi

# 10. Output is consumed as `KEY=value` by the workflow, so progress reporting
#     must stay on stderr.
fixture "ghcr.io/londonaicentre/flip-api:${SHA_TAG}
ghcr.io/londonaicentre/flare-fl-server:${SHA_TAG}" \
    "flip-api:41" "ghcr.io/londonaicentre/flip-api:sha-9999999" \
    "fl-server-net-1:12" "ghcr.io/londonaicentre/flare-fl-server:sha-9999999"
run_resolve "stdout carries only KEY=value lines"
if [[ "$(printf '%s\n' "${STDOUT}" | grep -cvE '^(DOCKER_TAG|DOCKER_FL_TAG)=')" -eq 0 ]]; then
    ok "stdout is exactly the two assignments"
else
    no "stdout is exactly the two assignments" "stdout: ${STDOUT}"
fi

echo ""
echo "==== ${PASS} passed, ${FAIL} failed ===="
[[ "${FAIL}" -eq 0 ]]
