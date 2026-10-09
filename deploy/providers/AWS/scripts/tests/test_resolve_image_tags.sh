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
# `buildx imagetools inspect --format '{{.Manifest.Digest}}' <ref>` is what the
# resolver uses to turn a release tag into the digest it pins — the TOP-LEVEL
# descriptor, which is the only shape this reports (unlike `manifest inspect
# --verbose`, whose per-platform entries are a different digest each).
if [[ "$1" == "buildx" && "$2" == "imagetools" && "$3" == "inspect" ]]; then
    shift 3
    while [[ "$1" == --* ]]; do shift 2; done
    ref="$1"
    if [[ -s "${FIXTURE_DIR}/docker-error" ]]; then
        cat "${FIXTURE_DIR}/docker-error" >&2
        exit 1
    fi
    grep -Fxq "${ref}" "${FIXTURE_DIR}/published" 2>/dev/null || {
        echo "ERROR: manifest unknown" >&2
        exit 1
    }
    # Per-repository digests: `flare-fl-api:v1` and `flare-fl-server:v1` are
    # different blobs, and the resolver must never carry one's digest onto the
    # other. `digest.<repo-basename>` overrides the shared `digest` fixture.
    repo="${ref%:*}"
    repo="${repo##*/}"
    digest="sha256:$(printf 'a%.0s' {1..64})"
    [[ -s "${FIXTURE_DIR}/digest" ]] && digest="$(cat "${FIXTURE_DIR}/digest")"
    [[ -s "${FIXTURE_DIR}/digest.${repo}" ]] && digest="$(cat "${FIXTURE_DIR}/digest.${repo}")"
    printf '%s\n' "${digest}"
    exit 0
fi
if [[ "$1" == "manifest" && "$2" == "inspect" ]]; then
    verbose=""
    if [[ "$3" == "--verbose" ]]; then
        verbose=1
        shift
    fi
    if [[ -s "${FIXTURE_DIR}/docker-error" ]]; then
        cat "${FIXTURE_DIR}/docker-error" >&2
        exit 1
    fi
    if grep -Fxq "$3" "${FIXTURE_DIR}/published" 2>/dev/null; then
        if [[ -n "${verbose}" ]]; then
            echo '[{"Ref":"'"$3"'","Descriptor":{"digest":"sha256:deadbeef"}}]'
            exit 0
        fi
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
#
# The fl-api-net-1 service and the fl-api repository are mirrored from the
# fl-server ones unless a case overrides the fixture files afterwards: the two FL
# images are resolved independently (FLIP#1283) but move together in every
# ordinary case, and mirroring keeps the pre-existing cases asserting exactly
# what they asserted before.
fixture() {
    FIXTURE_DIR="${TEST_ROOT}/fx-${RANDOM}"
    mkdir -p "${FIXTURE_DIR}"
    {
        printf '%s\n' "$1"
        # Same tags, fl-api repository.
        printf '%s\n' "$1" | sed -e 's/flare-fl-server:/flare-fl-api:/' -e 's/flower-superlink:/flower-fl-api:/'
    } >"${FIXTURE_DIR}/published"
    printf '%s' "$2" >"${FIXTURE_DIR}/flip-api.taskdef"
    printf '%s' "$3" >"${FIXTURE_DIR}/flip-api.image"
    printf '%s' "$4" >"${FIXTURE_DIR}/fl-server-net-1.taskdef"
    printf '%s' "$5" >"${FIXTURE_DIR}/fl-server-net-1.image"
    printf '%s' "${4:+fl-api-net-1:${4##*:}}" >"${FIXTURE_DIR}/fl-api-net-1.taskdef"
    printf '%s' "$(printf '%s' "$5" | sed -e 's/flare-fl-server/flare-fl-api/' -e 's/flower-superlink/flower-fl-api/')" \
        >"${FIXTURE_DIR}/fl-api-net-1.image"
    : >"${FIXTURE_DIR}/aws-exit"
    : >"${FIXTURE_DIR}/aws-failure-reason"
    : >"${FIXTURE_DIR}/docker-error"
    : >"${FIXTURE_DIR}/digest"
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
if [[ "$(printf '%s\n' "${STDOUT}" | grep -cvE '^(DOCKER_TAG|DOCKER_FL_TAG|DOCKER_FL_API_TAG)=')" -eq 0 ]]; then
    ok "stdout is exactly the three assignments"
else
    no "stdout is exactly the three assignments" "stdout: ${STDOUT}"
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

# 18. On an LZA estate DOCKER_REGISTRY is the account's ECR pull-through cache of
#     GHCR, which the runner holds no login for — probing it failed every apply
#     with "no basic auth credentials". The probe goes to GHCR, where the build
#     publishes; the cache mirrors it tag for tag. The live images carry the ECR
#     prefix, and the fallback still reads their tag.
ECR_GHCR="111122223333.dkr.ecr.eu-west-2.amazonaws.com/ghcr/londonaicentre/"
fixture "ghcr.io/londonaicentre/flip-api:${SHA_TAG}" \
    "flip-api:41" "${ECR_GHCR}flip-api:sha-9999999" \
    "fl-server-net-1:12" "${ECR_GHCR}flare-fl-server:v0.11.0"
run_resolve "an ECR pull-through registry is probed at its GHCR upstream" DOCKER_REGISTRY="${ECR_GHCR}"
expect_rc 0 "succeeds"
expect_tag DOCKER_TAG "${SHA_TAG}"
expect_tag DOCKER_FL_TAG "v0.11.0"
if [[ "${STDERR}" == *"looking for ghcr.io/londonaicentre/flip-api:${SHA_TAG}"* ]]; then
    ok "the probe names the GHCR reference"
else
    no "the probe names the GHCR reference" "stderr: ${STDERR}"
fi

# 18b. Any other registry — ECR without the ghcr/ cache prefix included — is
#      probed as given, not rewritten.
ECR_OWN="111122223333.dkr.ecr.eu-west-2.amazonaws.com/flip/"
fixture "${ECR_OWN}flip-api:${SHA_TAG}
${ECR_OWN}flare-fl-server:${SHA_TAG}" \
    "flip-api:41" "${ECR_OWN}flip-api:sha-9999999" \
    "fl-server-net-1:12" "${ECR_OWN}flare-fl-server:sha-9999999"
run_resolve "a registry that is not a GHCR cache is probed as given" DOCKER_REGISTRY="${ECR_OWN}"
expect_tag DOCKER_TAG "${SHA_TAG}"
expect_tag DOCKER_FL_TAG "${SHA_TAG}"

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

# ---------------------------------------------------------------------------
# RELEASE_TAG — the release-aware path (FLIP#1283). Everything below is new
# behaviour that only engages when RELEASE_TAG is set; case 25 is the guard that
# the unset path is still exactly what it was.
# ---------------------------------------------------------------------------

DIGEST="sha256:1111111111111111111111111111111111111111111111111111111111111111"
FL_DIGEST="sha256:2222222222222222222222222222222222222222222222222222222222222222"

# 19. RELEASE SET AND PUBLISHED. Pin the DIGEST the release tag resolves to,
#     carrying the release name with it: `:v<X.Y.Z>` is republished by design, so
#     a bare tag pin would let a re-run change what production pulls with no
#     apply (FLIP#751), while `v<X.Y.Z>@sha256:…` keeps the name legible and the
#     content fixed.
fixture "ghcr.io/londonaicentre/flip-api:v1.2.3
ghcr.io/londonaicentre/flare-fl-server:v1.2.3" \
    "flip-api:41" "ghcr.io/londonaicentre/flip-api:sha-9999999" \
    "fl-server-net-1:12" "ghcr.io/londonaicentre/flare-fl-server:sha-8888888"
printf '%s' "${DIGEST}" >"${FIXTURE_DIR}/digest"
run_resolve "RELEASE_TAG present — pinned by digest" RELEASE_TAG=v1.2.3
expect_rc 0 "exits 0"
expect_tag DOCKER_TAG "v1.2.3@${DIGEST}"
expect_tag DOCKER_FL_TAG "v1.2.3@${DIGEST}"

# ...and the sha tag must not win even when it is also published — the whole
# point is that the release build, not the branch build, is what gets deployed.
fixture "ghcr.io/londonaicentre/flip-api:v1.2.3
ghcr.io/londonaicentre/flip-api:${SHA_TAG}
ghcr.io/londonaicentre/flare-fl-server:v1.2.3
ghcr.io/londonaicentre/flare-fl-server:${SHA_TAG}" \
    "flip-api:41" "ghcr.io/londonaicentre/flip-api:sha-9999999" \
    "fl-server-net-1:12" "ghcr.io/londonaicentre/flare-fl-server:sha-8888888"
printf '%s' "${DIGEST}" >"${FIXTURE_DIR}/digest"
run_resolve "the release tag beats a published sha tag" RELEASE_TAG=v1.2.3
expect_tag DOCKER_TAG "v1.2.3@${DIGEST}"
if [[ "${STDOUT}" == *"${SHA_TAG}"* ]]; then
    no "never pins the sha build when a release was named" "stdout: ${STDOUT}"
else
    ok "never pins the sha build when a release was named"
fi

# 20. RELEASE SET AND MISSING. Fail closed. Every fallback is wrong here: the
#     caller only sets RELEASE_TAG once the release builds concluded, so absence
#     is a fault, and falling back would leave the environment on the PREVIOUS
#     release while reporting success — a silent no-op release.
fixture "ghcr.io/londonaicentre/flip-api:${SHA_TAG}" \
    "flip-api:41" "ghcr.io/londonaicentre/flip-api:sha-9999999" \
    "fl-server-net-1:12" "ghcr.io/londonaicentre/flare-fl-server:sha-8888888"
run_resolve "RELEASE_TAG absent from the registry is fatal" RELEASE_TAG=v9.9.9
if [[ "${RC}" -ne 0 ]]; then
    ok "exits non-zero when the release image is missing"
else
    no "exits non-zero when the release image is missing" "exit ${RC}, stdout: ${STDOUT}"
fi
if [[ -n "${STDOUT}" ]]; then
    no "emits no tag at all on a missing release" "stdout: ${STDOUT}"
else
    ok "emits no tag at all on a missing release"
fi
if [[ "${STDERR}" == *"v9.9.9"* && "${STDERR}" == *"not published"* && "${STDERR}" == *"re-run"* ]]; then
    ok "the error names the release and what to re-run"
else
    no "the error names the release and what to re-run" "stderr: ${STDERR}"
fi

# 21. A malformed RELEASE_TAG is rejected by name. Letting it through would
#     produce "release image absent", which reads as a failed build and sends
#     someone to re-run one that was fine.
for bad in v1.2 1.2.3 v1.2.3-rc1 latest; do
    fixture "" "flip-api:41" "ghcr.io/londonaicentre/flip-api:sha-9999999" \
        "fl-server-net-1:12" "ghcr.io/londonaicentre/flare-fl-server:sha-8888888"
    run_resolve "malformed RELEASE_TAG '${bad}' is rejected" RELEASE_TAG="${bad}"
    if [[ "${RC}" -ne 0 && "${STDERR}" == *"RELEASE_TAG"* ]]; then
        ok "rejects RELEASE_TAG='${bad}'"
    else
        no "rejects RELEASE_TAG='${bad}'" "exit ${RC}, stderr: ${STDERR}"
    fi
done

# 22. A registry that answers with something other than a positive absence is
#     still fatal on the release path — same fail-closed rule as the sha probe,
#     and here an outage read as "absent" would stop a release rather than
#     mis-deploy it, but the message must still be the honest one.
fixture "ghcr.io/londonaicentre/flip-api:v1.2.3" \
    "flip-api:41" "ghcr.io/londonaicentre/flip-api:sha-9999999" \
    "fl-server-net-1:12" "ghcr.io/londonaicentre/flare-fl-server:sha-8888888"
printf 'unauthorized: authentication required' >"${FIXTURE_DIR}/docker-error"
run_resolve "a registry error on the release probe is fatal" RELEASE_TAG=v1.2.3
if [[ "${RC}" -ne 0 && "${STDERR}" == *"without reporting the image as absent"* ]]; then
    ok "a registry error is not read as a missing release"
else
    no "a registry error is not read as a missing release" "exit ${RC}, stderr: ${STDERR}"
fi

# 23. The release tag exists but the registry returns no usable digest. Pinning
#     the bare tag instead would quietly reintroduce the mutability this path
#     exists to remove, so it must stop.
fixture "ghcr.io/londonaicentre/flip-api:v1.2.3
ghcr.io/londonaicentre/flare-fl-server:v1.2.3" \
    "flip-api:41" "ghcr.io/londonaicentre/flip-api:sha-9999999" \
    "fl-server-net-1:12" "ghcr.io/londonaicentre/flare-fl-server:sha-8888888"
printf 'sha256:not-a-digest' >"${FIXTURE_DIR}/digest"
run_resolve "an unusable digest is fatal, never a tag pin" RELEASE_TAG=v1.2.3
if [[ "${RC}" -ne 0 && "${STDERR}" == *"no usable digest"* ]]; then
    ok "refuses to fall back to a mutable tag pin"
else
    no "refuses to fall back to a mutable tag pin" "exit ${RC}, stderr: ${STDERR}"
fi

# 24. PLAN / DRIFT WITH A DIGEST-PINNED SERVICE. Once a release apply has written
#     `repo:v<X.Y.Z>@sha256:…`, plan and drift must read that reference back
#     WHOLE. Returning just `v1.2.3` would un-pin the digest on the next
#     infrastructure-only apply; returning nothing would report a permanent,
#     unclearable diff (and hold every apply on the FL gate) and leave
#     rollback-centralhub without a reference point.
fixture "" \
    "flip-api:41" "ghcr.io/londonaicentre/flip-api:v1.2.3@${DIGEST}" \
    "fl-server-net-1:12" "ghcr.io/londonaicentre/flare-fl-server:v1.2.3@${FL_DIGEST}"
run_resolve "plan reads a digest-pinned service" RESOLVE_SHA_TAG=false
expect_rc 0 "exits 0"
expect_tag DOCKER_TAG "v1.2.3@${DIGEST}"
expect_tag DOCKER_FL_TAG "v1.2.3@${FL_DIGEST}"

# ...and the same on an apply where nothing new was published: the running
# digest pin is reused rather than discarded.
fixture "" \
    "flip-api:41" "ghcr.io/londonaicentre/flip-api:v1.2.3@${DIGEST}" \
    "fl-server-net-1:12" "ghcr.io/londonaicentre/flare-fl-server:v1.2.3@${FL_DIGEST}"
run_resolve "an infra-only apply keeps the digest pin"
expect_rc 0 "exits 0"
expect_tag DOCKER_TAG "v1.2.3@${DIGEST}"

# ...while a BARE digest reference still yields nothing: ecs_tasks.tf builds
# `"${registry}<image>:${tag}"`, so there is no tag string that can express it,
# and inventing one would mint an unpullable reference. (Case 6 covers the
# short-digest form; this is the well-formed one.)
fixture "" \
    "flip-api:41" "ghcr.io/londonaicentre/flip-api@${DIGEST}" \
    "fl-server-net-1:12" "ghcr.io/londonaicentre/flare-fl-server:sha-8888888"
run_resolve "a bare digest reference has no tag to reuse" RESOLVE_SHA_TAG=false
expect_rc 0 "exits 0"
expect_tag DOCKER_TAG "stag"

# 25. SHIPS DARK. With RELEASE_TAG unset — and explicitly set to the empty string,
#     which is what an unset workflow_dispatch input expands to — the output is
#     exactly what it was before FLIP#1283.
fixture "ghcr.io/londonaicentre/flip-api:${SHA_TAG}
ghcr.io/londonaicentre/flip-api:v1.2.3" \
    "flip-api:41" "ghcr.io/londonaicentre/flip-api:sha-9999999" \
    "fl-server-net-1:12" "ghcr.io/londonaicentre/flare-fl-server:sha-8888888"
run_resolve "RELEASE_TAG unset — unchanged behaviour"
BASELINE="${STDOUT}"
expect_tag DOCKER_TAG "${SHA_TAG}"
expect_tag DOCKER_FL_TAG "sha-8888888"
run_resolve "RELEASE_TAG empty is the same as unset" RELEASE_TAG=
if [[ "${RC}" -eq 0 && "${STDOUT}" == "${BASELINE}" ]]; then
    ok "an empty RELEASE_TAG takes the pre-FLIP#1283 path byte for byte"
else
    no "an empty RELEASE_TAG takes the pre-FLIP#1283 path byte for byte" \
        "exit ${RC}" "got: ${STDOUT}" "want: ${BASELINE}"
fi

# ---------------------------------------------------------------------------
# 26. EVERY IMAGE GETS ITS OWN DIGEST (FLIP#1283 review). A digest belongs to one
#     repository: `flare-fl-api:v1.2.3` and `flare-fl-server:v1.2.3` are
#     different blobs. Carrying the server's digest onto the API mints
#     `flare-fl-api:v1.2.3@sha256:<fl-server digest>`, which ECS pulls BY DIGEST
#     — a CannotPullContainer on fl-api-net-1 after a green resolve and a green
#     apply. Distinct digests per repository here is the assertion that catches
#     it; a shared fixture cannot.
# ---------------------------------------------------------------------------
API_DIGEST="sha256:3333333333333333333333333333333333333333333333333333333333333333"
HUB_DIGEST="sha256:4444444444444444444444444444444444444444444444444444444444444444"

fixture "ghcr.io/londonaicentre/flip-api:v1.2.3
ghcr.io/londonaicentre/flare-fl-server:v1.2.3" \
    "flip-api:41" "ghcr.io/londonaicentre/flip-api:sha-9999999" \
    "fl-server-net-1:12" "ghcr.io/londonaicentre/flare-fl-server:sha-8888888"
printf '%s' "${HUB_DIGEST}" >"${FIXTURE_DIR}/digest.flip-api"
printf '%s' "${FL_DIGEST}" >"${FIXTURE_DIR}/digest.flare-fl-server"
printf '%s' "${API_DIGEST}" >"${FIXTURE_DIR}/digest.flare-fl-api"
run_resolve "each repository is pinned with its OWN digest" RELEASE_TAG=v1.2.3
expect_rc 0 "exits 0"
expect_tag DOCKER_TAG "v1.2.3@${HUB_DIGEST}"
expect_tag DOCKER_FL_TAG "v1.2.3@${FL_DIGEST}"
expect_tag DOCKER_FL_API_TAG "v1.2.3@${API_DIGEST}"

# ...and the same under flower, where the two FL repositories are
# flower-superlink and flower-fl-api.
fixture "ghcr.io/londonaicentre/flip-api:v1.2.3
ghcr.io/londonaicentre/flower-superlink:v1.2.3" \
    "flip-api:41" "ghcr.io/londonaicentre/flip-api:sha-9999999" \
    "fl-server-net-1:12" "ghcr.io/londonaicentre/flower-superlink:sha-8888888"
printf '%s' "${FL_DIGEST}" >"${FIXTURE_DIR}/digest.flower-superlink"
printf '%s' "${API_DIGEST}" >"${FIXTURE_DIR}/digest.flower-fl-api"
FL_BACKEND_UNDER_TEST=flower run_resolve "flower pins flower-fl-api separately" RELEASE_TAG=v1.2.3
expect_tag DOCKER_FL_TAG "v1.2.3@${FL_DIGEST}"
expect_tag DOCKER_FL_API_TAG "v1.2.3@${API_DIGEST}"

# 27. "Dies if the release image is absent" has to cover the FL API too — it was
#     the half of the FL set nobody checked. The fl-server release is published
#     here and the fl-api one is not, which is exactly the shape of a half-failed
#     release build.
fixture "ghcr.io/londonaicentre/flip-api:v1.2.3
ghcr.io/londonaicentre/flare-fl-server:v1.2.3" \
    "flip-api:41" "ghcr.io/londonaicentre/flip-api:sha-9999999" \
    "fl-server-net-1:12" "ghcr.io/londonaicentre/flare-fl-server:sha-8888888"
# Drop the mirrored fl-api entry the fixture helper added.
grep -v 'flare-fl-api' "${FIXTURE_DIR}/published" >"${FIXTURE_DIR}/published.tmp"
mv "${FIXTURE_DIR}/published.tmp" "${FIXTURE_DIR}/published"
run_resolve "a missing FL API release image is fatal" RELEASE_TAG=v1.2.3
if [[ "${RC}" -ne 0 && "${STDERR}" == *"flare-fl-api:v1.2.3"* ]]; then
    ok "names the absent fl-api release image"
else
    no "names the absent fl-api release image" "exit ${RC}, stderr: ${STDERR}"
fi

# 28. The FL API resolves from its OWN service, not fl-server's. A merge that
#     rebuilt only fl-api must pin the sha tag there and leave the server where
#     it is, and vice versa.
fixture "ghcr.io/londonaicentre/flare-fl-api:${SHA_TAG}" \
    "flip-api:41" "ghcr.io/londonaicentre/flip-api:sha-9999999" \
    "fl-server-net-1:12" "ghcr.io/londonaicentre/flare-fl-server:sha-8888888"
printf 'ghcr.io/londonaicentre/flare-fl-api:sha-7777777' >"${FIXTURE_DIR}/fl-api-net-1.image"
run_resolve "only the FL API image published"
expect_tag DOCKER_FL_TAG "sha-8888888"
expect_tag DOCKER_FL_API_TAG "${SHA_TAG}"

# ...and a running fl-api on a different tag from fl-server is reported as it is,
# rather than inheriting the server's.
fixture "" \
    "flip-api:41" "ghcr.io/londonaicentre/flip-api:sha-9999999" \
    "fl-server-net-1:12" "ghcr.io/londonaicentre/flare-fl-server:sha-8888888"
printf 'ghcr.io/londonaicentre/flare-fl-api:sha-7777777' >"${FIXTURE_DIR}/fl-api-net-1.image"
run_resolve "the FL API reuses its own live tag"
expect_tag DOCKER_FL_TAG "sha-8888888"
expect_tag DOCKER_FL_API_TAG "sha-7777777"

# 29. A missing fl-api service falls back on its own terms, and an AWS error
#     reading it is still fatal — the fail-closed rule applies to the third
#     lookup exactly as to the first two.
fixture "" \
    "flip-api:41" "ghcr.io/londonaicentre/flip-api:sha-9999999" \
    "fl-server-net-1:12" "ghcr.io/londonaicentre/flare-fl-server:sha-8888888"
: >"${FIXTURE_DIR}/fl-api-net-1.taskdef"
run_resolve "an absent FL API service takes the configured tag"
expect_rc 0 "exits 0"
expect_tag DOCKER_FL_TAG "sha-8888888"
expect_tag DOCKER_FL_API_TAG "stag"

# 30. A RELEASE_TAG with surrounding whitespace, or spelled as a git ref, is a
#     named error rather than a confusing "release image absent" — the review
#     asked for both shapes explicitly.
for bad in " v1.2.3" "v1.2.3 " "refs/tags/v1.2.3" "V1.2.3" "v1.2.3.4"; do
    fixture "" "flip-api:41" "ghcr.io/londonaicentre/flip-api:sha-9999999" \
        "fl-server-net-1:12" "ghcr.io/londonaicentre/flare-fl-server:sha-8888888"
    run_resolve "RELEASE_TAG '${bad}' is rejected" RELEASE_TAG="${bad}"
    if [[ "${RC}" -ne 0 && "${STDERR}" == *"RELEASE_TAG"* ]]; then
        ok "rejects RELEASE_TAG='${bad}'"
    else
        no "rejects RELEASE_TAG='${bad}'" "exit ${RC}, stderr: ${STDERR}"
    fi
done

# 31. RESOLVE_SHA_TAG=false IGNORES RELEASE_TAG. Plan and drift must read what is
#     deployed: probing the release would both report a diff no apply has written
#     and re-impose a registry login on paths that have none. The claim was in
#     the header but untested.
fixture "ghcr.io/londonaicentre/flip-api:v1.2.3
ghcr.io/londonaicentre/flare-fl-server:v1.2.3" \
    "flip-api:41" "ghcr.io/londonaicentre/flip-api:sha-9999999" \
    "fl-server-net-1:12" "ghcr.io/londonaicentre/flare-fl-server:sha-8888888"
printf '%s' "${DIGEST}" >"${FIXTURE_DIR}/digest"
run_resolve "plan ignores RELEASE_TAG" RESOLVE_SHA_TAG=false RELEASE_TAG=v1.2.3
expect_rc 0 "exits 0"
expect_tag DOCKER_TAG "sha-9999999"
expect_tag DOCKER_FL_TAG "sha-8888888"
# fl-api is resolved separately but from the same running pair, so plan/drift must
# report its live tag too — the review's point was that it was never asserted.
expect_tag DOCKER_FL_API_TAG "sha-8888888"
if [[ "${STDOUT}" == *"v1.2.3"* ]]; then
    no "never pins the release on a plan/drift run" "stdout: ${STDOUT}"
else
    ok "never pins the release on a plan/drift run"
fi

# ...and it still needs no docker: the same `env -i` PATH as case 16, now with
# RELEASE_TAG set. A release probe here would fail the case outright.
echo ""
echo "-- RESOLVE_SHA_TAG=false with RELEASE_TAG needs no docker"
if [[ -x "${NO_DOCKER}/bash" && -x "${NO_DOCKER}/jq" ]]; then
    out="$(env -i PATH="${NO_DOCKER}" FIXTURE_DIR="${FIXTURE_DIR}" \
        GIT_SHA="${GIT_SHA}" DOCKER_REGISTRY="ghcr.io/londonaicentre/" \
        FALLBACK_DOCKER_TAG=stag FALLBACK_DOCKER_FL_TAG=stag FL_BACKEND=nvflare \
        RESOLVE_SHA_TAG=false RELEASE_TAG=v1.2.3 \
        "${NO_DOCKER}/bash" "${SCRIPT}" 2>"${TEST_ROOT}/err")"
    rc=$?
    if [[ "${rc}" -eq 0 && "${out}" == *"DOCKER_TAG=sha-9999999"* ]]; then
        ok "a plan with RELEASE_TAG set touches no registry"
    else
        no "a plan with RELEASE_TAG set touches no registry" "exit ${rc}: ${out}" \
            "stderr: $(cat "${TEST_ROOT}/err")"
    fi
else
    no "a plan with RELEASE_TAG set touches no registry" "could not build a minimal PATH for the case"
fi

# 32. A MISSING RELEASE IMAGE DOES NOT WAIT. The caller sets RELEASE_TAG only
#     after the release builds concluded, so absence is already a certain
#     failure; burning the full GHCR_WAIT_SECONDS only delays it. Timed with a
#     real budget — the other release cases run at GHCR_WAIT_SECONDS=0, where a
#     wait and a single probe are indistinguishable.
fixture "" "flip-api:41" "ghcr.io/londonaicentre/flip-api:sha-9999999" \
    "fl-server-net-1:12" "ghcr.io/londonaicentre/flare-fl-server:sha-8888888"
started="$(date +%s)"
run_resolve "a missing release image fails immediately" \
    RELEASE_TAG=v9.9.9 GHCR_WAIT_SECONDS=6 GHCR_POLL_SECONDS=2
elapsed=$(($(date +%s) - started))
if [[ "${RC}" -ne 0 && "${elapsed}" -lt 4 ]]; then
    ok "probes once instead of waiting the budget (${elapsed}s)"
else
    no "probes once instead of waiting the budget" "exit ${RC} after ${elapsed}s"
fi

# 33. THE TWO FL IMAGES SHARE ONE WAIT BUDGET. fl-server and fl-api are published
#     by one workflow, so once fl-server has waited GHCR_WAIT_SECONDS out in full,
#     fl-api probes once rather than spending a second full budget — otherwise an
#     apply that touches only hub code waits 3x the advertised half hour.
fixture "" "flip-api:41" "ghcr.io/londonaicentre/flip-api:sha-9999999" \
    "fl-server-net-1:12" "ghcr.io/londonaicentre/flare-fl-server:stag"
started="$(date +%s)"
run_resolve "the FL images share one wait budget" GHCR_WAIT_SECONDS=6 GHCR_POLL_SECONDS=2
elapsed=$(($(date +%s) - started))
# flip-api and fl-server each wait the full 6s; fl-api must not add a third.
if [[ "${RC}" -eq 0 && "${elapsed}" -lt 17 ]]; then
    ok "fl-api probes once after fl-server's wait (${elapsed}s)"
else
    no "fl-api probes once after fl-server's wait" "exit ${RC} after ${elapsed}s"
fi
expect_tag DOCKER_FL_API_TAG "stag"

echo ""
echo "==== ${PASS} passed, ${FAIL} failed ===="
[[ "${FAIL}" -eq 0 ]]
