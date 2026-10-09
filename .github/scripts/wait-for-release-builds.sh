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
# Wait for the image builds release.yml dispatched at a release tag (FLIP#1283).
#
# This is a file rather than an inline `run:` block so it can be exercised
# against a stub `gh` — the wait is the step that decides whether production is
# re-pinned, and a substring assertion over the workflow's YAML proves nothing
# about it.
#
# IDENTIFYING THE RIGHT RUNS. `--commit "$HEAD_SHA"` alone cannot: the tagged
# commit is also main's head, so main's branch builds of the same commit report
# the same `headSha`, and five of the image workflows run from `workflow_run`
# after main's test suites — minutes after STARTED_AT, so a createdAt filter does
# not separate them either. Clearing the wait on one of those would release the
# apply while the `:v<X.Y.Z>` build is still running (the apply then dies in the
# resolver), and a branch run that ended `skipped` because the tests went red
# would report a perfectly good release as failed.
#
# The runs this step is waiting for are the ones it dispatched at the tag, and
# only those present `event == "workflow_dispatch"` AND `headBranch == <tag>`
# together. createdAt is kept on top, to separate this attempt from a previous
# release attempt's runs on the same commit.
#
# A POLL FAILURE IS NOT A BUILD FAILURE. One 502 from `gh run list` used to abort
# the whole wait under `set -e`, with no `::error::`, and the re-run rebuilt all
# twelve images. A failed poll now reads as "still pending"; only
# BUILD_POLL_MAX_FAILURES consecutive failures (a real outage, or a token that
# lost its scope) stop the release, with a message saying which it was.
#
# Reads from the environment:
#     GH_TOKEN                 token for `gh`
#     TAG                      v<X.Y.Z> — also the dispatched runs' headBranch
#     HEAD_SHA                 the tagged commit
#     WORKFLOWS                space-separated workflow file names to wait for
#     STARTED_AT               ISO-8601 instant just before the first dispatch
#     BUILD_WAIT_SECONDS       total budget (default 5400)
#     BUILD_POLL_SECONDS       gap between polls (default 30)
#     BUILD_POLL_MAX_FAILURES  consecutive poll failures tolerated (default 10)
#
# WHAT A RE-RUN COSTS. Every message below used to end "re-run this workflow".
# That is rarely the right advice: the GitHub Release does not exist yet when the
# wait fails, so a re-run goes back through the dispatch step and rebuilds all
# twelve images, overwriting `:v<X.Y.Z>` with fresh digests (the builds are not
# reproducible). Any site that already pulled `:v<X.Y.Z>` then holds a different
# digest from the one the hub pins. And a red build is usually fixed by a new
# commit, which a re-run of the old run cannot pick up and which the main-HEAD
# check would refuse anyway. So the messages name the manual path instead: wait
# for the builds to go green on their own, then dispatch the apply by hand.

set -euo pipefail

: "${TAG:?TAG is required}"
: "${HEAD_SHA:?HEAD_SHA is required}"
: "${STARTED_AT:?STARTED_AT is required}"
WORKFLOWS="${WORKFLOWS:-}"
BUILD_WAIT_SECONDS="${BUILD_WAIT_SECONDS:-5400}"
BUILD_POLL_SECONDS="${BUILD_POLL_SECONDS:-30}"
BUILD_POLL_MAX_FAILURES="${BUILD_POLL_MAX_FAILURES:-10}"

# The images are already building (or built); what is lost is only this job's
# knowledge of them. Re-running this workflow would re-dispatch all twelve and
# republish :TAG with new digests, so point at the apply instead.
manual_apply_hint() {
    echo "::notice::Do NOT re-run this workflow to recover: the Release does not exist yet, so a re-run re-dispatches all twelve builds and republishes ${TAG} with different digests than any site has already pulled. Watch the builds at the tag instead, and once they are green dispatch the apply yourself: gh workflow run terraform_apply.yml --ref main -f release_tag=${TAG}"
}

read -r -a pending <<<"${WORKFLOWS}"
[[ ${#pending[@]} -gt 0 ]] || {
    echo "::error::no builds were dispatched — nothing to wait for, and production was NOT re-pinned to ${TAG}"
    exit 1
}
[[ "${BUILD_POLL_SECONDS}" -ge 1 ]] || BUILD_POLL_SECONDS=1

deadline=$((SECONDS + BUILD_WAIT_SECONDS))
red=()
consecutive_failures=0

while :; do
    still=()
    poll_failed=0
    for wf in "${pending[@]}"; do
        if ! run="$(gh run list --workflow "${wf}" --commit "${HEAD_SHA}" --limit 20 \
            --json databaseId,status,conclusion,createdAt,url,event,headBranch \
            --jq "[.[] | select(.event == \"workflow_dispatch\" and .headBranch == \"${TAG}\" and .createdAt >= \"${STARTED_AT}\")] | first // empty" 2>&1)"; then
            echo "⚠️  could not list runs of ${wf} (treating as pending): ${run}"
            poll_failed=1
            still+=("${wf}")
            continue
        fi
        if [[ -z "${run}" ]]; then
            still+=("${wf}")
            continue
        fi
        status="$(jq -r '.status' <<<"${run}")"
        if [[ "${status}" != "completed" ]]; then
            still+=("${wf}")
            continue
        fi
        conclusion="$(jq -r '.conclusion' <<<"${run}")"
        url="$(jq -r '.url' <<<"${run}")"
        if [[ "${conclusion}" == "success" ]]; then
            echo "✅ ${wf} — ${url}"
        else
            echo "❌ ${wf} — ${conclusion} — ${url}"
            red+=("${wf} (${conclusion}) ${url}")
        fi
    done

    if [[ "${poll_failed}" -eq 1 ]]; then
        consecutive_failures=$((consecutive_failures + 1))
        if [[ "${consecutive_failures}" -ge "${BUILD_POLL_MAX_FAILURES}" ]]; then
            echo "::error::${consecutive_failures} consecutive failures listing workflow runs — giving up on ${TAG}. This is an API or token problem, not a failed build; production was NOT re-pinned, and the images may well be fine."
            manual_apply_hint
            exit 1
        fi
    else
        consecutive_failures=0
    fi

    # Break before the assignment rather than after it: `("${still[@]}")` on an
    # empty array is an unbound-variable error under `set -u` on bash < 4.4,
    # which is what a Mac still ships (FLIP#1395 fixed the same shape).
    [[ ${#still[@]} -eq 0 ]] && break
    pending=("${still[@]}")

    if [[ "${SECONDS}" -ge "${deadline}" ]]; then
        echo "::error::timed out after ${BUILD_WAIT_SECONDS}s waiting for ${pending[*]} at ${TAG} — production was NOT re-pinned to ${TAG}"
        manual_apply_hint
        exit 1
    fi
    echo "⏳ waiting on ${#pending[@]}: ${pending[*]}"
    sleep "${BUILD_POLL_SECONDS}"
done

if [[ ${#red[@]} -gt 0 ]]; then
    printf '::error::release build failed: %s\n' "${red[@]}"
    echo "::error::production was NOT re-pinned to ${TAG}. A re-run of this workflow will not help: it cannot pick up a fix (that is a new commit, which the main-HEAD check would refuse at this tag), and it would republish ${TAG} from the same red source. Fix forward with a new release, or — if the build was flaky rather than wrong — re-run just the failed build workflows at the tag and then dispatch the apply: gh workflow run terraform_apply.yml --ref main -f release_tag=${TAG}"
    exit 1
fi
echo "every dispatched build at ${TAG} is green"
