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

# Decide which image tags an automated Terraform apply should bake into the ECS
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
#     GHCR_WAIT_SECONDS          bound on waiting for the image (default 900)
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
GHCR_WAIT_SECONDS="${GHCR_WAIT_SECONDS:-900}"
GHCR_POLL_SECONDS="${GHCR_POLL_SECONDS:-30}"
# A zero poll interval never advances the elapsed counter, so a non-zero wait
# budget would spin forever rather than time out.
[[ "${GHCR_POLL_SECONDS}" -ge 1 ]] || GHCR_POLL_SECONDS=1

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
image_exists() {
    docker manifest inspect "$1" >/dev/null 2>&1
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

# Tag on the image the named service is running right now. Empty if the service
# does not exist, has no task definition, or carries no matching container.
active_tag() {
    local service="$1" container="$2"
    local task_def image

    task_def="$(aws ecs describe-services \
        --cluster "${ECS_CLUSTER}" --services "${service}" \
        --query 'services[0].taskDefinition' --output text 2>/dev/null || true)"
    [[ -n "${task_def}" && "${task_def}" != "None" ]] || return 0

    image="$(aws ecs describe-task-definition \
        --task-definition "${task_def}" \
        --query "taskDefinition.containerDefinitions[?name=='${container}'].image | [0]" \
        --output text 2>/dev/null || true)"
    [[ -n "${image}" && "${image}" != "None" ]] || return 0

    # Strip the repository, keep the tag. A digest reference (`@sha256:…`) has no
    # tag to reuse, so report nothing rather than a fragment.
    [[ "${image}" == *"@"* ]] && return 0
    printf '%s' "${image##*:}"
}

# Resolve one tag: published sha tag, else the live tag, else the configured one.
resolve() {
    local label="$1" image_name="$2" service="$3" container="$4" fallback="$5"
    local ref="${DOCKER_REGISTRY}${image_name}:${SHA_TAG}"

    log "🔎 ${label}: looking for ${ref}"
    if wait_for_image "${ref}"; then
        log "   pinning ${SHA_TAG}"
        printf '%s' "${SHA_TAG}"
        return 0
    fi

    log "   not published within ${GHCR_WAIT_SECONDS}s — this merge probably changed no service code."
    local live
    live="$(active_tag "${service}" "${container}")"
    if [[ -n "${live}" ]]; then
        log "   reusing the tag ${service} is already running: ${live}"
        printf '%s' "${live}"
        return 0
    fi

    # Reaching here with a running service would mean silently reverting to a
    # mutable tag, so only an account with no service at all may land here.
    log "   no running ${service} to read a tag from — first apply into this account."
    log "   falling back to the configured tag: ${fallback}"
    printf '%s' "${fallback}"
}

hub_tag="$(resolve "hub images" "flip-api" "flip-api" "flip-api" "${FALLBACK_DOCKER_TAG}")"
# The fl-server container is named for its net, not for the service role
# (ecs_tasks.tf:275) — same string as the service, which is easy to mis-assume.
fl_tag="$(resolve "FL images" "${FL_SERVER_IMAGE}" "fl-server-net-1" "fl-server-net-1" "${FALLBACK_DOCKER_FL_TAG}")"

echo "DOCKER_TAG=${hub_tag}"
echo "DOCKER_FL_TAG=${fl_tag}"
