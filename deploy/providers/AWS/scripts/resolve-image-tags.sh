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

# Decide which image tags an automated Terraform run should bake into the ECS
# task definitions.
#
# The problem this solves (FLIP#962, hazard A). ecs_services.tf tracks
# max(terraform_revision, live_revision), so a *no-op* apply preserves whatever
# `make deploy-centralhub` last deployed. But an apply that actually changes a
# task definition mints revision N+1 from `var.docker_image_tag` — and the env
# files set that to the mutable `:stag` / `:prod`. Applying with those silently
# un-pins the immutable `sha-<short7>` tag that FLIP#751 introduced, so what is
# running stops being a recorded commit and `make rollback-centralhub` loses its
# reference point.
#
# The fix is to resolve the tag rather than read it from config:
#
#   1. `sha-<short7>` of the commit being applied, once its image is published.
#      A merge to main then pins the exact release build, and Terraform and
#      deploy-centralhub agree by construction rather than by convention.
#   2. If that image never appears — the common case where a merge touched only
#      infrastructure, so no docker_build_*.yml ran — fall back to the tag the
#      currently-ACTIVE task definition carries. That is the image already
#      serving traffic, so the apply is a genuine no-op for the container.
#   3. Only if there is no service yet (first apply into an empty account) fall
#      back to the configured tag.
#
# The precise guarantee is about *substitution*, not about the string: step 3 is
# never reached while a service is running, so the configured tag can never
# replace a deployed one. Step 2 reuses whatever is actually deployed — and if an
# environment is currently running the mutable `:stag` (staging is, today), that
# is what comes back, because reusing it is a genuine no-op. What cannot happen
# is an apply quietly moving a sha-pinned service onto a mutable tag.
#
# THAT GUARANTEE ONLY HOLDS IF EVERY LOOKUP FAILS CLOSED. An expired session, a
# throttle, an AccessDenied or a wrong ECS_CLUSTER must never be mistaken for
# "there is no service here", because that answer sends the resolver to step 3
# and emits the mutable tag on an account that is very much running one. So the
# AWS calls are checked, absence is recognised only from ECS's own
# `failures[].reason == "MISSING"`, and anything else stops the run.
#
# Plan and drift resolve the same way (RESOLVE_SHA_TAG=false), for a different
# reason: once an apply has written a sha pin into a task definition, a plan that
# reads the configured `:prod` reports a permanent, unclearable diff on the FL
# task definitions — which the FL gate then holds every apply on.
#
# RELEASE_TAG — being told the release instead of racing for it (FLIP#1283).
#
# A release merge to main is applied by the ordinary push-triggered run, which
# pins `sha-<short7>`: that image is built by the branch build, so its baked
# FLIP_RELEASE is the sha and the hub reports a commit rather than `v<X.Y.Z>`.
# The `:v<X.Y.Z>` images (same source, FLIP_RELEASE=v<X.Y.Z>) are a *separate*
# set of builds dispatched at the tag, and they do not exist yet when that apply
# runs — so the resolver cannot discover the release by looking. It has to be
# told, by a later dispatch fired once those builds are green (PR 2 of FLIP#1283).
#
# When RELEASE_TAG is set, the resolver:
#
#   * probes `:${RELEASE_TAG}` instead of the sha tag, and
#   * DIES if it is absent, rather than falling back. Absence is a real fault
#     here (the caller only sets RELEASE_TAG once the release builds concluded),
#     and the old fallbacks would silently leave production on the *previous*
#     release with a green run — a worse failure than the cosmetic one this
#     fixes.
#   * pins the DIGEST the release tag currently resolves to, as
#     `v<X.Y.Z>@sha256:…`, not the bare tag. `:v<X.Y.Z>` is republished by
#     design (a release re-run rebuilds and re-pushes it), and ECS pulls at task
#     start, so a tag pin could change what production runs with no apply and no
#     audit trail — the FLIP#751 guarantee. The `tag@digest` form keeps the
#     release name legible in the task definition (and `repo:tag@digest` is a
#     valid reference, so ecs_tasks.tf's `"${registry}flip-api:${tag}"` needs no
#     change) while what is pulled is immutable.
#
# A DIGEST IS PER-REPOSITORY, SO EVERY IMAGE IS RESOLVED SEPARATELY. There are
# three of them — the hub (flip-api), the FL server and the FL API — and
# `flare-fl-api:v1.2.3` and `flare-fl-server:v1.2.3` are different blobs with
# different digests. Resolving the FL pair as one value and applying the server's
# digest to the API would mint `flare-fl-api:v1.2.3@sha256:<fl-server digest>`:
# ECS pulls by digest and ignores the tag, so fl-api-net-1 fails with
# CannotPullContainer after a green resolve and a green apply, and `active_tag`
# then reads the bad reference back on every later infrastructure-only apply.
# Hence three outputs (DOCKER_TAG / DOCKER_FL_TAG / DOCKER_FL_API_TAG) and
# Terraform's separate `fl_api_image_tag` variable.
#
# When RELEASE_TAG is unset, nothing below behaves differently — the release path
# is entirely additive and ships dark until a caller sets it.
#
# Usage:
#     resolve-image-tags.sh
#
# Reads from the environment:
#     GIT_SHA                    commit being applied (full sha)
#     RELEASE_TAG                optional v<X.Y.Z>; when set, pin that release by
#                                digest and fail closed if it is not published
#     DOCKER_REGISTRY            e.g. ghcr.io/londonaicentre/, or an ECR
#                                pull-through cache of it (probed upstream)
#     FALLBACK_DOCKER_TAG        configured hub tag (:stag / :prod)
#     FALLBACK_DOCKER_FL_TAG     configured FL tag
#     FL_BACKEND                 nvflare | flower — selects the FL image name
#     ECS_CLUSTER                default flip-cluster
#     RESOLVE_SHA_TAG            true (default) to look for this commit's image;
#                                false to resolve the live tag only, which needs
#                                no registry access at all (plan, drift)
#     GHCR_WAIT_SECONDS          bound on waiting for the image (default 900);
#                                0 probes once and moves on
#     GHCR_POLL_SECONDS          gap between probes (default 30)
#
# Writes `KEY=value` lines to stdout:
#     DOCKER_TAG=...
#     DOCKER_FL_TAG=...
#     DOCKER_FL_API_TAG=...

set -euo pipefail

die() {
    echo "❌ $*" >&2
    exit 1
}

log() { echo "$*" >&2; }

: "${GIT_SHA:?GIT_SHA is required}"
: "${DOCKER_REGISTRY:?DOCKER_REGISTRY is required}"
: "${FALLBACK_DOCKER_TAG:?FALLBACK_DOCKER_TAG is required}"
: "${FALLBACK_DOCKER_FL_TAG:?FALLBACK_DOCKER_FL_TAG is required}"
: "${FL_BACKEND:?FL_BACKEND is required}"

# Where the tag is probed. On a platform-managed (LZA) estate DOCKER_REGISTRY is
# the account's ECR pull-through cache for GHCR
# (<account>.dkr.ecr.<region>.amazonaws.com/ghcr/<org>/): what ECS pulls from,
# but not where "published" is decided, and the runner holds no ECR login for it
# — `docker manifest inspect` there fails with "no basic auth credentials". The
# cache mirrors GHCR tag for tag, so the probe goes upstream; the tag pinned is
# the same either way.
PROBE_REGISTRY="${DOCKER_REGISTRY}"
if [[ "${DOCKER_REGISTRY}" =~ ^[0-9]{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/ghcr/(.+)$ ]]; then
    PROBE_REGISTRY="ghcr.io/${BASH_REMATCH[1]}"
fi

ECS_CLUSTER="${ECS_CLUSTER:-flip-cluster}"
RESOLVE_SHA_TAG="${RESOLVE_SHA_TAG:-true}"
GHCR_WAIT_SECONDS="${GHCR_WAIT_SECONDS:-900}"
GHCR_POLL_SECONDS="${GHCR_POLL_SECONDS:-30}"
# A zero poll interval never advances the elapsed counter, so a non-zero wait
# budget would spin forever rather than time out.
[[ "${GHCR_POLL_SECONDS}" -ge 1 ]] || GHCR_POLL_SECONDS=1

command -v jq >/dev/null 2>&1 || die "jq is required (the ECS responses are parsed as JSON so a failure is distinguishable from an absence)"

case "${RESOLVE_SHA_TAG}" in
    true | false) ;;
    *) die "RESOLVE_SHA_TAG must be 'true' or 'false' (got '${RESOLVE_SHA_TAG}')" ;;
esac

# Unset and empty mean the same thing — the dispatch input is an empty string on
# an ordinary push-triggered run, and that must take the pre-FLIP#1283 path
# rather than probe `:` .
RELEASE_TAG="${RELEASE_TAG:-}"
if [[ -n "${RELEASE_TAG}" ]]; then
    # Validated here rather than at the probe so a typo is a named error instead
    # of a confusing "release image absent" — the latter reads as "the release
    # build failed" and sends someone to re-run a build that was fine.
    [[ "${RELEASE_TAG}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] ||
        die "RELEASE_TAG must be a stable release tag of the form v<X>.<Y>.<Z> (got '${RELEASE_TAG}')"
fi

# Must match the `sha-<short7>` tag docker_build_*.yml publishes.
SHA_TAG="sha-${GIT_SHA:0:7}"

# Must match deploy/fl_backend.mk's DOCKER_FL_SERVER_NAME / DOCKER_FL_API_NAME,
# which are what Terraform receives as var.fl_server_name / var.fl_api_name.
case "${FL_BACKEND}" in
    nvflare)
        FL_SERVER_IMAGE="flare-fl-server"
        FL_API_IMAGE="flare-fl-api"
        ;;
    flower)
        FL_SERVER_IMAGE="flower-superlink"
        FL_API_IMAGE="flower-fl-api"
        ;;
    *) die "FL_BACKEND must be 'nvflare' or 'flower' (got '${FL_BACKEND}')" ;;
esac

# Probe the registry rather than the build workflow: the tag existing is the
# condition that actually matters, and it stays true however the image got there
# (a rerun, a workflow_dispatch, a backfill).
#
# Returns 0 published, 1 genuinely absent. Anything else — a registry outage, an
# expired GHCR token, a rate limit — stops the run rather than reading as "not
# published", which would send the caller down the fallback path on a lie.
image_exists() {
    local ref="$1" out rc=0
    out="$(docker manifest inspect "${ref}" 2>&1)" || rc=$?
    [[ "${rc}" -eq 0 ]] && return 0

    # tr, not ${out,,}: lowercase expansion is bash 4, and macOS ships bash 3.2.
    local lowered
    lowered="$(printf '%s' "${out}" | tr '[:upper:]' '[:lower:]')"
    case "${lowered}" in
        *"manifest unknown"* | *"manifest_unknown"* | *"no such manifest"* | \
            *"not found"* | *"name unknown"* | *"name_unknown"*)
            return 1
            ;;
    esac
    die "docker manifest inspect ${ref} failed (exit ${rc}) without reporting the image as absent.
   Treating that as 'not published' would fall back to a tag this commit never built. Output:
   ${out}"
}

# Wait for one image, bounded. Returns 0 if it appeared, 1 if the budget ran out.
wait_for_image() {
    local ref="$1"
    local waited=0
    while true; do
        if image_exists "${ref}"; then
            [[ "${waited}" -gt 0 ]] && log "   ${ref} appeared after ${waited}s."
            return 0
        fi
        if [[ "${waited}" -ge "${GHCR_WAIT_SECONDS}" ]]; then
            return 1
        fi
        sleep "${GHCR_POLL_SECONDS}"
        waited=$((waited + GHCR_POLL_SECONDS))
    done
}

# Digest `ref` currently resolves to, into IMAGE_DIGEST (`sha256:<64 hex>`).
#
# This must be the digest of what the TAG points at — the manifest *list* digest
# for a multi-arch image — because that is the reference `repo:tag@digest` pulls.
# `docker manifest inspect --verbose` cannot give it: for a manifest list it
# returns one entry per platform and each `.Descriptor.digest` is that
# platform's manifest (and the first entry is frequently an `unknown/unknown`
# attestation, not amd64). Pinning one of those would pin a single-platform —
# often non-runnable — artefact. `docker buildx imagetools inspect` reports the
# top-level descriptor directly, in one shape for both single manifests and
# lists.
#
# Fatal on anything unexpected: a digest is the whole point of the release pin,
# so a missing or malformed one must stop the run rather than quietly degrade to
# a mutable tag pin.
IMAGE_DIGEST=""

image_digest() {
    local ref="$1" out rc=0
    IMAGE_DIGEST=""
    out="$(docker buildx imagetools inspect --format '{{.Manifest.Digest}}' "${ref}" 2>&1)" || rc=$?
    if [[ "${rc}" -ne 0 ]]; then
        local hint=""
        docker buildx version >/dev/null 2>&1 ||
            hint="
   (docker buildx is not available here — it is what reads the manifest-list digest. Install the
   buildx plugin, or run this from CI, where the runner has it.)"
        die "docker buildx imagetools inspect ${ref} failed (exit ${rc}) while resolving the release digest. Output:
   ${out}${hint}"
    fi

    # --format prints the digest alone, but a warning line on stderr merged into
    # a caller's log is easy to produce; take the last non-empty line.
    IMAGE_DIGEST="$(printf '%s\n' "${out}" | sed -n 's/^[[:space:]]*\(sha256:[0-9a-f]\{64\}\)[[:space:]]*$/\1/p' | tail -n 1)"

    [[ "${IMAGE_DIGEST}" =~ ^sha256:[0-9a-f]{64}$ ]] ||
        die "no usable digest for ${ref} (got '${out}').
   Refusing to pin the mutable tag instead: a republished :${RELEASE_TAG} would then change what
   production runs with no apply and no audit trail (FLIP#751)."
}

# Tag on the image the named service is running right now, into ACTIVE_TAG.
#
# Leaves ACTIVE_TAG empty only for the three genuine "there is no tag to reuse"
# cases: ECS says the service is MISSING, the task definition carries no
# container of that name, or the deployed reference is digest-pinned. Every other
# outcome — any non-zero aws exit, any failure reason other than MISSING, a
# response that is neither a service nor a failure — is fatal.
#
# It assigns to a global rather than printing, and `resolve` does the same, so
# that `die` runs in the shell that has to stop. Called as `$(active_tag …)` the
# exit would only end the substitution's subshell: bash does not carry errexit
# back out of one reliably, and the caller would sail on to the fallback — which
# is the exact fail-open this rewrite exists to remove.
ACTIVE_TAG=""

active_tag() {
    local service="$1" container="$2"
    local services_json task_def_json reason task_def image tag digest name
    ACTIVE_TAG=""

    services_json="$(aws ecs describe-services \
        --cluster "${ECS_CLUSTER}" --services "${service}" --output json 2>&1)" ||
        die "aws ecs describe-services failed for '${service}' on cluster '${ECS_CLUSTER}'.
   Refusing to guess: an API error here is indistinguishable from an empty account, and
   guessing 'empty' un-pins the released image (FLIP#751). Output:
   ${services_json}"

    reason="$(jq -r '.failures[0].reason // empty' <<<"${services_json}" 2>/dev/null)" ||
        die "could not parse the describe-services response for '${service}'"

    if [[ -n "${reason}" ]]; then
        [[ "${reason}" == "MISSING" ]] ||
            die "ECS reported '${reason}' for service '${service}' on cluster '${ECS_CLUSTER}' — only MISSING means the service genuinely does not exist"
        return 0
    fi

    task_def="$(jq -r '.services[0].taskDefinition // empty' <<<"${services_json}")"
    [[ -n "${task_def}" && "${task_def}" != "None" ]] ||
        die "describe-services returned neither a service nor a failure for '${service}'"

    task_def_json="$(aws ecs describe-task-definition --task-definition "${task_def}" --output json 2>&1)" ||
        die "aws ecs describe-task-definition failed for '${task_def}'. Output:
   ${task_def_json}"

    image="$(jq -r --arg c "${container}" \
        'first(.taskDefinition.containerDefinitions[] | select(.name == $c) | .image) // empty' \
        <<<"${task_def_json}")"
    [[ -n "${image}" && "${image}" != "None" ]] || return 0

    # Strip the repository, keep what can be pinned again.
    #
    # Three shapes matter:
    #   repo:tag                 → `tag` (the ordinary case)
    #   repo:tag@sha256:…        → `tag@sha256:…`, the release pin this script
    #                              writes (FLIP#1283). It must come back whole:
    #                              returning just `tag` would un-pin the digest on
    #                              the next infrastructure-only apply, and
    #                              returning nothing would make plan/drift report
    #                              a permanent diff and lose rollback's reference
    #                              point.
    #   repo@sha256:…            → nothing. A bare digest reference cannot be
    #                              re-expressed through ecs_tasks.tf's
    #                              `"${registry}<image>:${tag}"`, so there is no
    #                              tag to reuse; inventing one would mint an
    #                              unpullable reference.
    # An untagged reference (`ghcr.io/org/flip-api`, or
    # `registry.example:5000/flip-api`, where the only colon is the registry
    # port) likewise reports nothing.
    if [[ "${image}" == *"@"* ]]; then
        digest="${image##*@}"
        name="${image%@*}"
        # Only a well-formed digest is reusable; anything else is not something
        # to hand back to Terraform as a tag.
        [[ "${digest}" =~ ^sha256:[0-9a-f]{64}$ ]] || return 0
        tag="${name##*:}"
        [[ "${tag}" != "${name}" ]] || return 0
        [[ "${tag}" != */* ]] || return 0
        ACTIVE_TAG="${tag}@${digest}"
        return 0
    fi
    tag="${image##*:}"
    [[ "${tag}" != "${image}" ]] || return 0
    [[ "${tag}" != */* ]] || return 0
    ACTIVE_TAG="${tag}"
}

# Wait groups whose publishing workflow has already been waited out in full.
# fl-server and fl-api are built and pushed by one workflow
# (fl-docker-build-<backend>.yml), so a full timeout on the first is a timeout
# on the second; the sibling then probes once instead of spending a second
# GHCR_WAIT_SECONDS.
WAIT_GROUPS_EXHAUSTED=""

# Resolve one tag into RESOLVED_TAG: published sha tag, else the live tag, else
# the configured one. Assigns to a global for the reason given above active_tag.
RESOLVED_TAG=""

resolve() {
    local label="$1" image_name="$2" service="$3" container="$4" fallback="$5" wait_group="${6:-}"
    local ref="${PROBE_REGISTRY}${image_name}:${SHA_TAG}"
    RESOLVED_TAG=""

    if [[ "${RESOLVE_SHA_TAG}" == "true" ]]; then
        if [[ -n "${RELEASE_TAG}" ]]; then
            # Told, not raced (FLIP#1283): the caller sets RELEASE_TAG only once
            # the release builds have concluded green, so absence here is a real
            # fault and every fallback below is wrong — taking one would leave
            # production on the previous release and report success.
            local release_ref="${PROBE_REGISTRY}${image_name}:${RELEASE_TAG}"
            log "🔎 ${label}: resolving release ${RELEASE_TAG} — ${release_ref}"
            # A single probe, not wait_for_image: the caller sets RELEASE_TAG
            # only after the release builds concluded, so the image is either
            # there now or this run is already wrong. Waiting the full
            # GHCR_WAIT_SECONDS would only delay a certain failure.
            image_exists "${release_ref}" ||
                die "${label}: ${release_ref} is not published.
   RELEASE_TAG was set, so this image is expected to exist; refusing to fall back to the sha tag or
   to the running one, which would leave this environment on the previous release with a green run.
   Re-run the release build for ${image_name} at ${RELEASE_TAG} (release.yml dispatches it), then
   re-run this apply."
            image_digest "${release_ref}"
            # tag@digest: immutable pull, legible release name. See the header.
            RESOLVED_TAG="${RELEASE_TAG}@${IMAGE_DIGEST}"
            log "   pinning ${RESOLVED_TAG}"
            return 0
        fi
        if [[ -n "${wait_group}" && " ${WAIT_GROUPS_EXHAUSTED} " == *" ${wait_group} "* ]]; then
            # One workflow publishes every image in this group, so its sha tags
            # appear together or not at all. A sibling has already waited the
            # full GHCR_WAIT_SECONDS for that workflow and timed out; waiting
            # again would only add another GHCR_WAIT_SECONDS to a run that is
            # already going to fall back. Probe once instead (FLIP#1283).
            log "🔎 ${label}: single probe for ${ref} — ${wait_group} already waited ${GHCR_WAIT_SECONDS}s"
            if image_exists "${ref}"; then
                log "   pinning ${SHA_TAG}"
                RESOLVED_TAG="${SHA_TAG}"
                return 0
            fi
            log "   not published — this merge probably changed no ${wait_group} code."
        else
            log "🔎 ${label}: looking for ${ref}"
            if wait_for_image "${ref}"; then
                log "   pinning ${SHA_TAG}"
                RESOLVED_TAG="${SHA_TAG}"
                return 0
            fi
            [[ -z "${wait_group}" ]] || WAIT_GROUPS_EXHAUSTED="${WAIT_GROUPS_EXHAUSTED} ${wait_group}"
            log "   not published within ${GHCR_WAIT_SECONDS}s — this merge probably changed no service code."
        fi
    else
        # Plan and drift: what matters is agreeing with the deployed task
        # definition, not with a build that may not exist for this commit. A
        # RELEASE_TAG is deliberately ignored here — plan and drift must read
        # what is deployed, and probing would also re-impose a GHCR login on
        # paths that have none.
        log "🔎 ${label}: reading the tag ${service} is running (RESOLVE_SHA_TAG=false)"
    fi

    active_tag "${service}" "${container}"
    if [[ -n "${ACTIVE_TAG}" ]]; then
        log "   reusing the tag ${service} is already running: ${ACTIVE_TAG}"
        RESOLVED_TAG="${ACTIVE_TAG}"
        return 0
    fi

    # Reaching here means ECS positively reported the service MISSING (or a
    # deployed reference with no tag in it). Every error path above is fatal, so
    # this can no longer be reached by an AWS call that merely failed.
    log "   no running ${service} to read a tag from — first apply into this account."
    log "   falling back to the configured tag: ${fallback}"
    RESOLVED_TAG="${fallback}"
}

resolve "hub images" "flip-api" "flip-api" "flip-api" "${FALLBACK_DOCKER_TAG}"
hub_tag="${RESOLVED_TAG}"
# The fl-server container is named for its net, not for the service role
# (ecs_tasks.tf:275) — same string as the service, which is easy to mis-assume.
resolve "FL server image" "${FL_SERVER_IMAGE}" "fl-server-net-1" "fl-server-net-1" "${FALLBACK_DOCKER_FL_TAG}" "fl"
fl_tag="${RESOLVED_TAG}"
# Resolved separately from the server: same release tag, different repository,
# therefore a different digest. See the header. Same wait group, though — one
# workflow publishes both, so the second one never waits a second full budget.
resolve "FL API image" "${FL_API_IMAGE}" "fl-api-net-1" "fl-api-net-1" "${FALLBACK_DOCKER_FL_TAG}" "fl"
fl_api_tag="${RESOLVED_TAG}"

echo "DOCKER_TAG=${hub_tag}"
echo "DOCKER_FL_TAG=${fl_tag}"
echo "DOCKER_FL_API_TAG=${fl_api_tag}"
