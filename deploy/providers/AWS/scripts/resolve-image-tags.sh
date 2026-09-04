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
# Usage:
#     resolve-image-tags.sh
#
# Reads from the environment:
#     GIT_SHA                    commit being applied (full sha)
#     DOCKER_REGISTRY            e.g. ghcr.io/londonaicentre/
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

# Must match the `sha-<short7>` tag docker_build_*.yml publishes.
SHA_TAG="sha-${GIT_SHA:0:7}"

case "${FL_BACKEND}" in
    nvflare) FL_SERVER_IMAGE="flare-fl-server" ;;
    flower) FL_SERVER_IMAGE="flower-superlink" ;;
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

    local lowered="${out,,}"
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
    local services_json task_def_json reason task_def image tag
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

    # Strip the repository, keep the tag. Two references carry no tag to reuse
    # and must report nothing rather than a fragment: a digest reference
    # (`repo@sha256:…`), and an untagged one (`ghcr.io/org/flip-api`, or
    # `registry.example:5000/flip-api`, where the only colon is the registry
    # port). Returning either would mint an unpullable image reference.
    [[ "${image}" == *"@"* ]] && return 0
    tag="${image##*:}"
    [[ "${tag}" != "${image}" ]] || return 0
    [[ "${tag}" != */* ]] || return 0
    ACTIVE_TAG="${tag}"
}

# Resolve one tag into RESOLVED_TAG: published sha tag, else the live tag, else
# the configured one. Assigns to a global for the reason given above active_tag.
RESOLVED_TAG=""

resolve() {
    local label="$1" image_name="$2" service="$3" container="$4" fallback="$5"
    local ref="${DOCKER_REGISTRY}${image_name}:${SHA_TAG}"
    RESOLVED_TAG=""

    if [[ "${RESOLVE_SHA_TAG}" == "true" ]]; then
        log "🔎 ${label}: looking for ${ref}"
        if wait_for_image "${ref}"; then
            log "   pinning ${SHA_TAG}"
            RESOLVED_TAG="${SHA_TAG}"
            return 0
        fi
        log "   not published within ${GHCR_WAIT_SECONDS}s — this merge probably changed no service code."
    else
        # Plan and drift: what matters is agreeing with the deployed task
        # definition, not with a build that may not exist for this commit.
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
resolve "FL images" "${FL_SERVER_IMAGE}" "fl-server-net-1" "fl-server-net-1" "${FALLBACK_DOCKER_FL_TAG}"
fl_tag="${RESOLVED_TAG}"

echo "DOCKER_TAG=${hub_tag}"
echo "DOCKER_FL_TAG=${fl_tag}"
