#!/usr/bin/env bash
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
# Decide whether *this* run publishes the flip-ui bundle, and for which branch.
#
# FLIP#1186 runs the publish from two triggers, and this file is the rule that
# divides the work between them, so that a bundle is never published twice and
# never published before the API it calls:
#
#   * push to develop/main touching flip-ui/** — publish. The UI changed and
#     nothing about the deployment did. Waiting for an apply here would wait
#     forever, because terraform_apply.yml is filtered to deploy/providers/AWS/**.
#     This is the common case, and it publishes immediately.
#
#   * the Terraform Apply workflow completing on develop/main — publish only if
#     the commit it applied also touched flip-ui/**. That is the commit that
#     changed the API and the UI together, and the UI can call a route that only
#     exists once the apply has run — so the push trigger stands down for it (it
#     is also a deploy/providers/AWS/** change) and this branch publishes after
#     the apply instead. A *held* apply is included: the run still completes, and
#     the FL gate says nothing about whether the UI bundle should ship.
#
# Prints `key=value` lines for $GITHUB_OUTPUT: run, branch, ref, reason.
#
# Fail-open and fail-closed are chosen per branch rather than uniformly: on a push
# the UI source certainly changed (the workflow's own path filter), so an
# unreadable file list must not swallow the publish — a duplicate one is harmless,
# the same bytes go to the same key. After an apply the question is the opposite
# ("did this commit touch the UI at all?"), and a guess of "yes" would republish
# on every infrastructure merge, so there it stands down.
set -euo pipefail

EVENT="${EVENT:?EVENT must be set (push | workflow_run)}"
SHA="${SHA:?SHA must be set — the commit under consideration}"
BEFORE="${BEFORE:-}"
BRANCH="${BRANCH:?BRANCH must be set — the branch being deployed (develop | main)}"

case "${BRANCH}" in
    develop|main) ;;
    *) echo "::error::refusing to publish from branch '${BRANCH}' (expected develop or main)" >&2; exit 1 ;;
esac

# Distinguishes "the diff says nothing changed" (an empty list) from "the diff
# could not be taken at all", which the two triggers answer differently.
UNREADABLE="__unreadable__"

emit() {
    printf 'run=%s\nbranch=%s\nref=%s\nreason=%s\n' "$1" "${BRANCH}" "${SHA}" "$2"
}

# Which side of the diff to compare against: the previous tip on a push (the
# workflow starts from the commit's own tree), the commit's first parent after an
# apply (where the run's payload carries no `before`).
base="${SHA}^"
if [[ "${EVENT}" == "push" && "${BEFORE}" =~ ^[0-9a-f]{40}$ ]]; then
    base="${BEFORE}"
fi

# The paths are a group: for the push branch, "did anything under
# deploy/providers/AWS change" decides whether this run stands down; for the apply
# branch, "did flip-ui change" decides whether it runs at all.
changed() {
    git diff --name-only "${base}" "${SHA}" -- "$1" 2>/dev/null || return 1
}

if [[ "${EVENT}" == "push" ]]; then
    # Both questions are asked on a push: did the UI change (the workflow's path
    # filter says yes, but a caller can invoke this directly), and did anything
    # under deploy/providers/AWS change as well — which is what makes this run
    # stand down for the apply that follows.
    infra="$(changed deploy/providers/AWS)" || infra="${UNREADABLE}"
    ui="$(changed flip-ui)" || ui="${UNREADABLE}"
    if [[ "${infra}" == "${UNREADABLE}" || "${ui}" == "${UNREADABLE}" ]]; then
        echo "::warning title=deploy_ui::could not list the changed files for ${SHA}; publishing anyway (a duplicate publish is harmless, a missing one is a stale UI)"
        emit true "could not list the changed files for ${SHA} — publishing anyway"
    elif [[ -n "${infra}" ]]; then
        emit false "this push also changed deploy/providers/AWS — the apply that follows publishes the UI, after the API it may call"
    elif [[ -z "${ui}" ]]; then
        emit false "this push left flip-ui alone"
    else
        emit true "flip-ui changed on a push that changed no infrastructure"
    fi
    exit 0
fi

if ui="$(changed flip-ui)"; then
    if [[ -n "${ui}" ]]; then
        emit true "the applied commit also changed flip-ui — publishing now that the API it may call is deployed"
    else
        emit false "the applied commit left flip-ui alone"
    fi
else
    emit false "could not list the changed files for ${SHA} — not republishing a bundle this commit did not touch"
fi
