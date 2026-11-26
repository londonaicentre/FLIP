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
#     GitHub Release and send a re-run back through all fourteen image builds.
#   * still queued when the budget expires -> a warning, not a failure. Queuing
#     behind a long push apply is legitimate and the release is not wrong.
#   * the listing worked and shows no run for this tag by then -> a failure: the
#     dispatch produced nothing this step can vouch for. Only that. A listing that
#     errored, or a re-dispatch not listed yet, is a warning: the apply is most
#     likely queued, and failing here would withhold the GitHub Release from it.
#
# WHICH RUN IS THE RELEASE APPLY. Only a dispatch that carries this release_tag,
# which terraform_apply.yml's `run-name` puts in the run's title. An operator's
# dispatch on main in the same window (an `fl_quiesced: true` re-run without the
# tag) is not it, and accepting it left the release green with production on the
# previous one (FLIP#2047).
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

# terraform_apply.yml's run-name for a dispatch with release_tag set.
RUN_TITLE="Terraform Apply: release ${TAG}"

find_run() {
    gh run list --repo "${REPO}" --workflow terraform_apply.yml \
        --branch main --event workflow_dispatch --limit 20 \
        --json databaseId,status,conclusion,createdAt,url,displayTitle \
        --jq "[.[] | select(.createdAt >= \"${STARTED_AT}\" and .displayTitle == \"${RUN_TITLE}\")] | sort_by(.createdAt) | first // empty"
}

redispatched=0
deadline=$((SECONDS + APPLY_CONFIRM_SECONDS))

while :; do
    listed=1
    if ! run="$(find_run 2>&1)"; then
        echo "⚠️  could not list terraform_apply runs (treating as not yet visible): ${run}"
        run=""
        listed=0
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
                # The new run gets a budget of its own, not what is left of the first one's.
                deadline=$((SECONDS + APPLY_CONFIRM_SECONDS))
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

        # `pending` is a run held by the tf-apply-main group: not started, and still displaceable.
        if [[ "${status}" != "queued" && "${status}" != "waiting" && "${status}" != "requested" && "${status}" != "pending" ]]; then
            echo "✅ the release apply at ${TAG} is running — ${url}"
            echo "   its outcome is reported by that run, not by this one."
            exit 0
        fi
        echo "⏳ release apply queued (${status}) — ${url}"
    else
        echo "⏳ no terraform_apply run visible yet for ${TAG}"
    fi

    if [[ "${SECONDS}" -ge "${deadline}" ]]; then
        check="gh run list --repo ${REPO} --workflow terraform_apply.yml --branch main"
        if [[ "${listed}" -eq 0 || ( -z "${run}" && "${redispatched}" -eq 1 ) ]]; then
            echo "::warning::the release apply at ${TAG} is unconfirmed after ${APPLY_CONFIRM_SECONDS}s: the runs could not be listed, or the re-dispatched run was not listed yet. Check that a run titled '${RUN_TITLE}' started: ${check}"
            exit 0
        fi
        if [[ -z "${run}" ]]; then
            echo "::error::no terraform_apply run titled '${RUN_TITLE}' appeared within ${APPLY_CONFIRM_SECONDS}s of the dispatch, so nothing confirms production was re-pinned to ${TAG}. Look for it (${check}); if no run carries the tag, ${manual_hint}"
            exit 1
        fi
        echo "::warning::the release apply at ${TAG} was still queued after ${APPLY_CONFIRM_SECONDS}s. That is legitimate if an apply is in flight on main, but check it started and was not displaced: ${check}"
        exit 0
    fi
    sleep "${APPLY_CONFIRM_POLL_SECONDS}"
done
