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

# How long terraform_apply.yml lets resolve-image-tags.sh wait for each image
# group's `sha-<short7>` image (FLIP#1811).
#
# A group's image is built for this commit when its build workflow has a push run
# at this sha, so ask GitHub for that run instead of guessing from changed paths:
#
#   hub  test_flip_api.yml; a green run triggers docker_build_flip_api.yml
#   fl   fl-docker-build-<FL_BACKEND>.yml (fl-server, fl-api, Flower's proxy)
#
# A queued, running or green run gets the full GHCR_WAIT_SECONDS. No run, or a
# finished run that is not green, gets 0, which the resolver reads as one probe.
# GitHub creates a push's runs within a second of each other and the apply asks
# about a minute later, so the run queried is there by then; a dispatch of the
# same commit gets the same answer.
#
# Fails safe: an API or parse error waits the full budget. A release apply
# (RELEASE_TAG) is not asked about: its images come from builds dispatched at
# the tag, not from push runs, and the resolver probes them once whatever the
# budget.
#
# Not seen: a build started by hand ("Run workflow") after a red push run, or a
# red run re-run after the apply asked. Either gets one probe, so re-dispatch the
# apply once that build is green.
#
# Reads from the environment:
#     GITHUB_REPOSITORY, GITHUB_SHA  set by Actions
#     GH_TOKEN                       for `gh` (needs actions: read)
#     FL_BACKEND                     nvflare | flower
#     GHCR_WAIT_SECONDS              the full budget (default 900)
#     RELEASE_TAG                    optional v<X.Y.Z>
#
# Writes `hub=<seconds>` and `fl=<seconds>` to stdout, for $GITHUB_OUTPUT.

set -euo pipefail

die() {
    echo "❌ $*" >&2
    exit 1
}

log() { echo "$*" >&2; }

: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"
: "${GITHUB_SHA:?GITHUB_SHA is required}"
: "${FL_BACKEND:?FL_BACKEND is required}"
GHCR_WAIT_SECONDS="${GHCR_WAIT_SECONDS:-900}"
RELEASE_TAG="${RELEASE_TAG:-}"

[[ "${GHCR_WAIT_SECONDS}" =~ ^[0-9]+$ ]] ||
    die "GHCR_WAIT_SECONDS must be a whole number of seconds (got '${GHCR_WAIT_SECONDS}')"
GHCR_WAIT_SECONDS=$((10#${GHCR_WAIT_SECONDS})) # base 10: a leading zero is not octal
case "${FL_BACKEND}" in
    nvflare | flower) ;;
    *) die "FL_BACKEND must be 'nvflare' or 'flower' (got '${FL_BACKEND}')" ;;
esac

# building: a run that may still publish the image. none / red: nothing will.
# Anything that is not a list of runs, or a finished run with no conclusion, is
# an error, which waits the full budget.
VERDICT='
  .workflow_runs
  | if type != "array" then error("no workflow_runs list")
    elif any(.[]; .status == "completed" and (.conclusion | type) != "string")
    then error("completed run without a conclusion")
    elif any(.[]; .status != "completed" or .conclusion == "success") then "building"
    elif length == 0 then "none"
    else "red" end'

# Print the seconds to wait for one group's image.
budget() {
    local group="$1" workflow="$2" runs verdict
    if [[ -n "${RELEASE_TAG}" ]]; then
        log "${group}: release ${RELEASE_TAG}, runs not asked (the resolver probes the release once)"
        echo "${GHCR_WAIT_SECONDS}"
        return
    fi
    if ! runs="$(gh api "repos/${GITHUB_REPOSITORY}/actions/workflows/${workflow}/runs?head_sha=${GITHUB_SHA}&event=push")" ||
        ! verdict="$(jq -er "${VERDICT}" <<<"${runs}" 2>/dev/null)"; then
        log "::warning::${group}: could not read the ${workflow} runs for ${GITHUB_SHA:0:7}, so waiting the full ${GHCR_WAIT_SECONDS}s"
        echo "${GHCR_WAIT_SECONDS}"
        return
    fi
    case "${verdict}" in
        none)
            log "${group}: no ${workflow} run for ${GITHUB_SHA:0:7}, so probing once"
            echo 0
            ;;
        red)
            log "::warning::${group}: the ${workflow} run for ${GITHUB_SHA:0:7} did not succeed, so probing once: this apply keeps the running image unless that image is published"
            echo 0
            ;;
        *)
            log "${group}: ${workflow} has a queued, running or green run for ${GITHUB_SHA:0:7}, so waiting up to ${GHCR_WAIT_SECONDS}s"
            echo "${GHCR_WAIT_SECONDS}"
            ;;
    esac
}

echo "hub=$(budget hub test_flip_api.yml)"
echo "fl=$(budget fl "fl-docker-build-${FL_BACKEND}.yml")"
