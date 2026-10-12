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
# Confirm that the release apply release.yml dispatched is actually going to run
# (FLIP#1283).
#
# WHY A DISPATCH IS NOT A RUN. `concurrency: tf-apply-main` with
# cancel-in-progress: false protects the apply that is *already running*; it does
# not protect a run that is still queued in the group. GitHub keeps at most one
# pending run per group, so the next run queued on main replaces it — an
# operator's `fl_quiesced: true` re-dispatch of a held push apply, fired without
# `release_tag`, silently cancels the pending release apply. release.yml would
# stay green (the dispatch succeeded) and production would never move to the
# release. This step closes that gap: it watches the run the dispatch created and
# re-dispatches once if it is cancelled before it starts.
#
# WHAT IS AND IS NOT A FAILURE HERE.
#   * cancelled before starting  -> re-dispatch once, then fail if it happens again.
#   * started (in_progress), or concluded in any way  -> confirmed; this step's job
#     is done. The apply's own outcome is reported by the apply's own run, which
#     is red and visible; failing the release here as well would only block the
#     GitHub Release and send a re-run back through all twelve image builds.
#   * still queued when the budget expires -> a warning, not a failure. Queuing
#     behind a long push apply is legitimate and the release is not wrong.
#
# Reads from the environment:
#     GH_TOKEN                   token for `gh`
#     REPO                       owner/name
#     TAG                        v<X.Y.Z>, forwarded as release_tag
#     STARTED_AT                 ISO-8601 instant just before the dispatch
#     APPLY_CONFIRM_SECONDS      total budget (default 900)
#     APPLY_CONFIRM_POLL_SECONDS gap between polls (default 15)

set -euo pipefail

: "${REPO:?REPO is required}"
: "${TAG:?TAG is required}"
: "${STARTED_AT:?STARTED_AT is required}"
APPLY_CONFIRM_SECONDS="${APPLY_CONFIRM_SECONDS:-900}"
APPLY_CONFIRM_POLL_SECONDS="${APPLY_CONFIRM_POLL_SECONDS:-15}"
[[ "${APPLY_CONFIRM_POLL_SECONDS}" -ge 1 ]] || APPLY_CONFIRM_POLL_SECONDS=1

manual_hint="dispatch it again by hand once the group is clear: gh workflow run terraform_apply.yml --repo ${REPO} --ref refs/heads/main -f release_tag=${TAG}"

find_run() {
    gh run list --repo "${REPO}" --workflow terraform_apply.yml \
        --branch main --event workflow_dispatch --limit 20 \
        --json databaseId,status,conclusion,createdAt,url \
        --jq "[.[] | select(.createdAt >= \"${STARTED_AT}\")] | sort_by(.createdAt) | first // empty"
}

redispatched=0
deadline=$((SECONDS + APPLY_CONFIRM_SECONDS))

while :; do
    if ! run="$(find_run 2>&1)"; then
        echo "⚠️  could not list terraform_apply runs (treating as not yet visible): ${run}"
        run=""
    fi

    if [[ -n "${run}" ]]; then
        status="$(jq -r '.status' <<<"${run}")"
        conclusion="$(jq -r '.conclusion // ""' <<<"${run}")"
        url="$(jq -r '.url' <<<"${run}")"

        if [[ "${conclusion}" == "cancelled" ]]; then
            if [[ "${redispatched}" -eq 0 ]]; then
                echo "::warning::the release apply at ${TAG} was cancelled before it ran (${url}) — almost always another run queued on main displacing it in the tf-apply-main group. Dispatching it once more."
                STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
                redispatched=1
                if ! gh workflow run terraform_apply.yml --repo "${REPO}" \
                    --ref refs/heads/main -f release_tag="${TAG}"; then
                    echo "::error::re-dispatch of terraform_apply.yml for ${TAG} failed — production was NOT re-pinned. ${manual_hint}"
                    exit 1
                fi
                sleep "${APPLY_CONFIRM_POLL_SECONDS}"
                continue
            fi
            echo "::error::the release apply at ${TAG} was cancelled twice before running (${url}) — something keeps queuing applies on main. Production was NOT re-pinned; ${manual_hint}"
            exit 1
        fi

        if [[ "${status}" != "queued" && "${status}" != "waiting" && "${status}" != "requested" ]]; then
            echo "✅ the release apply at ${TAG} is running — ${url}"
            echo "   its outcome is reported by that run, not by this one."
            exit 0
        fi
        echo "⏳ release apply queued (${status}) — ${url}"
    else
        echo "⏳ no terraform_apply run visible yet for ${TAG}"
    fi

    if [[ "${SECONDS}" -ge "${deadline}" ]]; then
        echo "::warning::the release apply at ${TAG} was still queued after ${APPLY_CONFIRM_SECONDS}s. That is legitimate if an apply is in flight on main, but check it started and was not displaced: gh run list --repo ${REPO} --workflow terraform_apply.yml --branch main"
        exit 0
    fi
    sleep "${APPLY_CONFIRM_POLL_SECONDS}"
done
