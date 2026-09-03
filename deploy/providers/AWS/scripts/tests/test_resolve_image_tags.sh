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
# ${FIXTURE_DIR}/published, one reference per line, and otherwise fails the way
# a real registry reports an absent tag. ${FIXTURE_DIR}/docker-error switches it
# to the *other* kind of failure — an outage, a rate limit, an expired token —
# which must never be read as "not published".
cat >"${MOCKBIN}/docker" <<'MOCK_DOCKER'
#!/usr/bin/env bash
if [[ "$1" == "manifest" && "$2" == "inspect" ]]; then
    if [[ -s "${FIXTURE_DIR}/docker-error" ]]; then
        cat "${FIXTURE_DIR}/docker-error" >&2
        exit 1
    fi
    if grep -Fxq "$3" "${FIXTURE_DIR}/published" 2>/dev/null; then
        echo '{"schemaVersion":2}'
        exit 0
    fi
    echo "manifest unknown" >&2
    exit 1
fi
exit 1
MOCK_DOCKER

# aws: enough of `ecs describe-services` / `ecs describe-task-definition` to
# report what a service is running, in the JSON shape the resolver parses.
#   ${FIXTURE_DIR}/<service>.taskdef  — task definition ARN ("" => MISSING)
#   ${FIXTURE_DIR}/<service>.image    — image on the matching container
#   ${FIXTURE_DIR}/aws-exit           — exit with this code instead (API failure)
#   ${FIXTURE_DIR}/aws-failure-reason — report this failures[].reason instead of
#                                       MISSING (e.g. CLUSTER_NOT_FOUND)
cat >"${MOCKBIN}/aws" <<'MOCK_AWS'
#!/usr/bin/env bash
if [[ -s "${FIXTURE_DIR}/aws-exit" ]]; then
    echo "An error occurred (ExpiredTokenException) when calling the operation: token expired" >&2
    exit "$(cat "${FIXTURE_DIR}/aws-exit")"
fi
service="" taskdef=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --services) service="$2"; shift ;;
        --task-definition) taskdef="$2"; shift ;;
    esac
    shift
done
if [[ -n "${service}" ]]; then
    fx="${FIXTURE_DIR}/${service}.taskdef"
    if [[ ! -s "${fx}" ]]; then
        reason="MISSING"
        [[ -s "${FIXTURE_DIR}/aws-failure-reason" ]] && reason="$(cat "${FIXTURE_DIR}/aws-failure-reason")"
        printf '{"services":[],"failures":[{"arn":"%s","reason":"%s"}]}\n' "${service}" "${reason}"
        exit 0
    fi
    printf '{"services":[{"taskDefinition":"%s"}],"failures":[]}\n' "$(cat "${fx}")"
    exit 0
fi
if [[ -n "${taskdef}" ]]; then
    # taskdef fixture files hold "<service>:<revision>" so the image fixture can
    # be found, and the container is named for the service as it is in
    # ecs_tasks.tf.
    svc="${taskdef%%:*}"
    fx="${FIXTURE_DIR}/${svc}.image"
    if [[ ! -s "${fx}" ]]; then
        echo '{"taskDefinition":{"containerDefinitions":[]}}'
        exit 0
    fi
    printf '{"taskDefinition":{"containerDefinitions":[{"name":"%s","image":"%s"}]}}\n' \
        "${svc}" "$(cat "${fx}")"
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
    : >"${FIXTURE_DIR}/aws-exit"
    : >"${FIXTURE_DIR}/aws-failure-reason"
    : >"${FIXTURE_DIR}/docker-error"
    export FIXTURE_DIR
}

# Run the resolver against the current fixture. Trailing KEY=VALUE arguments are
# added to its environment — spelled through `env` rather than as an assignment
# prefix, because a prefix has to be literal at parse time: `"$@"` expanding to
# `RESOLVE_SHA_TAG=false` in that position is read as the *command* to run.
run_resolve() {
    local title="$1"
    shift
    echo ""
    echo "-- ${title}"
    STDOUT="$(env PATH="${MOCKBIN}:${PATH}" \
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

expect_rc() {
    local want="$1" what="$2"
    if [[ "${RC}" -eq "${want}" ]]; then
        ok "${what}"
    else
        no "${what}" "wanted exit ${want}, got ${RC}" "stderr: ${STDERR}"
    fi
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

# 11. FAIL CLOSED ON AN AWS ERROR. The invariant in the header only holds if
#     "no service" is distinguishable from "the call did not work". An expired
#     session, a throttle, an AccessDenied or a wrong --cluster all used to
#     return empty, and the resolver then printed the mutable configured tag with
#     exit 0 — the FLIP#751 un-pin, on an unattended production apply.
fixture "" \
    "flip-api:41" "ghcr.io/londonaicentre/flip-api:sha-9999999" \
    "fl-server-net-1:12" "ghcr.io/londonaicentre/flare-fl-server:sha-8888888"
printf '254' >"${FIXTURE_DIR}/aws-exit"
run_resolve "an aws error is fatal, never 'no service'"
if [[ "${RC}" -ne 0 ]]; then
    ok "exits non-zero when the ECS call fails"
else
    no "exits non-zero when the ECS call fails" "exit ${RC}, stdout: ${STDOUT}"
fi
if [[ "${STDOUT}" == *"=stag"* ]]; then
    no "does not emit the configured tag on an AWS error" "stdout: ${STDOUT}"
else
    ok "does not emit the configured tag on an AWS error"
fi
if [[ "${STDERR}" == *"describe-services failed"* ]]; then
    ok "names the failed call"
else
    no "names the failed call" "stderr: ${STDERR}"
fi

# 12. Only ECS's own MISSING means the service is absent. CLUSTER_NOT_FOUND is
#     the realistic misconfiguration — a wrong ECS_CLUSTER answers for every
#     service at once, and answering "empty account" to that is the same bug.
fixture "" "" "" "" ""
printf 'CLUSTER_NOT_FOUND' >"${FIXTURE_DIR}/aws-failure-reason"
run_resolve "a non-MISSING failure reason is fatal"
if [[ "${RC}" -ne 0 && "${STDERR}" == *"CLUSTER_NOT_FOUND"* ]]; then
    ok "rejects a failure reason other than MISSING"
else
    no "rejects a failure reason other than MISSING" "exit ${RC}, stderr: ${STDERR}"
fi

# 13. A registry that is down is not a registry that has not published. Reading
#     an outage as "no image" is harmless on its own (step 2 catches it) but it
#     is the same class of mistake, and on a first apply it would reach step 3.
fixture "ghcr.io/londonaicentre/flip-api:${SHA_TAG}
ghcr.io/londonaicentre/flare-fl-server:${SHA_TAG}" \
    "flip-api:41" "ghcr.io/londonaicentre/flip-api:sha-9999999" \
    "fl-server-net-1:12" "ghcr.io/londonaicentre/flare-fl-server:sha-8888888"
printf 'unauthorized: authentication required' >"${FIXTURE_DIR}/docker-error"
run_resolve "a registry error is fatal, never 'not published'"
if [[ "${RC}" -ne 0 && "${STDERR}" == *"without reporting the image as absent"* ]]; then
    ok "rejects a registry error that is not an absence"
else
    no "rejects a registry error that is not an absence" "exit ${RC}, stderr: ${STDERR}"
fi

# 14. An absent tag still reads as absent — the fail-closed check must not turn
#     the ordinary case into a failure.
fixture "" \
    "flip-api:41" "ghcr.io/londonaicentre/flip-api:sha-9999999" \
    "fl-server-net-1:12" "ghcr.io/londonaicentre/flare-fl-server:sha-8888888"
run_resolve "an unpublished tag is still just unpublished"
expect_rc 0 "exits 0"
expect_tag DOCKER_TAG "sha-9999999"

# 15. An UNTAGGED live image has no tag to reuse. `${image##*:}` returns the
#     whole reference when there is no colon at all, so the resolver used to
#     emit `ghcr.io/londonaicentre/flip-api` as a "tag" — and a registry port is
#     the same trap with a colon in the wrong place.
fixture "" \
    "flip-api:41" "ghcr.io/londonaicentre/flip-api" \
    "fl-server-net-1:12" "registry.example:5000/flare-fl-server"
run_resolve "an untagged live image does not become a tag"
expect_rc 0 "exits 0"
expect_tag DOCKER_TAG "stag"
expect_tag DOCKER_FL_TAG "stag"
if [[ "${STDOUT}" == *"londonaicentre"* || "${STDOUT}" == *"registry.example"* ]]; then
    no "never emits a repository path as a tag" "stdout: ${STDOUT}"
else
    ok "never emits a repository path as a tag"
fi

# 16. RESOLVE_SHA_TAG=false is what plan and drift use: resolve the live tag and
#     nothing else. Both images are published for this commit here, and the sha
#     tag must still NOT be chosen — a plan that pinned it would report a diff
#     against a task definition no apply has written yet.
fixture "ghcr.io/londonaicentre/flip-api:${SHA_TAG}
ghcr.io/londonaicentre/flare-fl-server:${SHA_TAG}" \
    "flip-api:41" "ghcr.io/londonaicentre/flip-api:sha-9999999" \
    "fl-server-net-1:12" "ghcr.io/londonaicentre/flare-fl-server:sha-8888888"
run_resolve "RESOLVE_SHA_TAG=false reads only the live tag" RESOLVE_SHA_TAG=false
expect_rc 0 "exits 0"
expect_tag DOCKER_TAG "sha-9999999"
expect_tag DOCKER_FL_TAG "sha-8888888"

# ...and it must not touch the registry at all, so plan and drift need neither a
# GHCR login nor `packages: read`. Proven by running it under an `env -i` PATH
# that holds only the binaries the live-tag path legitimately needs — no docker
# anywhere on it, so a stray `docker manifest inspect` would fail the case rather
# than quietly succeed against the mock.
NO_DOCKER="${TEST_ROOT}/nodocker"
mkdir -p "${NO_DOCKER}"
for bin in bash cat jq; do
    path="$(command -v "${bin}")" || {
        echo "   ⚠️  ${bin} not on PATH — skipping the no-docker case"
        path=""
    }
    [[ -n "${path}" ]] && ln -sf "${path}" "${NO_DOCKER}/${bin}"
done
ln -sf "${MOCKBIN}/aws" "${NO_DOCKER}/aws"
echo ""
echo "-- RESOLVE_SHA_TAG=false needs no docker on PATH"
if [[ -x "${NO_DOCKER}/bash" && -x "${NO_DOCKER}/jq" ]]; then
    out="$(env -i PATH="${NO_DOCKER}" FIXTURE_DIR="${FIXTURE_DIR}" \
        GIT_SHA="${GIT_SHA}" DOCKER_REGISTRY="ghcr.io/londonaicentre/" \
        FALLBACK_DOCKER_TAG=stag FALLBACK_DOCKER_FL_TAG=stag FL_BACKEND=nvflare \
        RESOLVE_SHA_TAG=false "${NO_DOCKER}/bash" "${SCRIPT}" 2>"${TEST_ROOT}/err")"
    rc=$?
    if [[ "${rc}" -eq 0 && "${out}" == *"DOCKER_TAG=sha-9999999"* ]]; then
        ok "resolves with no docker binary present"
    else
        no "resolves with no docker binary present" "exit ${rc}: ${out}" "stderr: $(cat "${TEST_ROOT}/err")"
    fi
else
    no "resolves with no docker binary present" "could not build a minimal PATH for the case"
fi

# 17. An unknown RESOLVE_SHA_TAG must stop rather than be read as falsy — a
#     typo'd "no" silently reverting plan to the waiting path would reintroduce
#     the 30-minute stall this flag exists to avoid.
echo ""
echo "-- unknown RESOLVE_SHA_TAG"
out="$(PATH="${MOCKBIN}:${PATH}" GIT_SHA="${GIT_SHA}" DOCKER_REGISTRY="ghcr.io/x/" \
    FALLBACK_DOCKER_TAG=stag FALLBACK_DOCKER_FL_TAG=stag FL_BACKEND=nvflare \
    RESOLVE_SHA_TAG=no GHCR_WAIT_SECONDS=0 GHCR_POLL_SECONDS=0 bash "${SCRIPT}" 2>&1 >/dev/null)"
rc=$?
if [[ "${rc}" -ne 0 && "${out}" == *"RESOLVE_SHA_TAG"* ]]; then
    ok "rejects an unknown RESOLVE_SHA_TAG"
else
    no "rejects an unknown RESOLVE_SHA_TAG" "exit ${rc}: ${out}"
fi

echo ""
echo "==== ${PASS} passed, ${FAIL} failed ===="
[[ "${FAIL}" -eq 0 ]]
