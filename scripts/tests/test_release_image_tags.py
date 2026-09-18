#!/usr/bin/env -S uv run --script
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
"""Every image-publishing workflow must fire on a release tag and push `:vX.Y.Z` (FLIP#1204).

A FLIP release is the `v<X.Y.Z>` git tag that release.yml creates on a push to main. Before
FLIP#1204 no image build listened for that tag: images only carried the mutable `:prod` /
`:stag` and the per-commit `sha-<short7>`, and because every build workflow is path-filtered a
"release" was a different sha per service with nothing tying them together. Trust sites pin
one release across the whole stack (`DOCKER_TAG=v0.6.0` in the kit's Hub-shared block), so
every image has to be built — changed or not — at the release commit and tagged with it.

These guards read the workflow files as text (like the chart invariants tests do) so a
workflow that quietly drops the trigger, or one added without it, fails CI here rather than
at the next release. The build-only flip-ui workflow publishes nothing and is excluded.

Usage:
    uv run scripts/tests/test_release_image_tags.py
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"

# Publishes to GHCR ⇒ must carry the release-tag trigger. flip-ui's build is a smoke test only.
IMAGE_WORKFLOWS = sorted(
    p
    for p in WORKFLOWS.glob("*.yml")
    if (p.name.startswith("docker_build_") and p.name != "docker_build_flip_ui.yml")
    or p.name.startswith("fl-docker-build-")
)

# `push:` block carrying a `tags:` list (flow or block style) whose entry is the platform
# release glob. The flip-utils train tags `flip-utils-v*` and must NOT trigger image builds,
# so the glob is asserted exactly rather than loosely.
_GLOB = r"['\"]v\*\.\*\.\*['\"]"
TAG_TRIGGER = re.compile(
    r"\n {2}push:\s*\n(?:[^\n]*\n)*? {4}tags:\s*(?:\[\s*" + _GLOB + r"\s*\]|\n\s+- " + _GLOB + r")"
)


class ReleaseImageTags(unittest.TestCase):
    def test_workflow_roster_is_not_empty(self) -> None:
        assert len(IMAGE_WORKFLOWS) >= 12, [p.name for p in IMAGE_WORKFLOWS]

    def test_every_image_workflow_triggers_on_release_tags(self) -> None:
        for wf in IMAGE_WORKFLOWS:
            text = wf.read_text()
            with self.subTest(workflow=wf.name):
                # Plain asserts with a short message: an assertRegex/assertIn failure would dump
                # the whole workflow file, burying the one line that matters.
                assert TAG_TRIGGER.search(text), f"{wf.name}: no `push.tags: ['v*.*.*']` trigger"
                assert "refs/tags/v" in text, f"{wf.name}: the tag step never branches on refs/tags/v*"

    def test_release_tag_never_moves_prod_or_stag(self) -> None:
        """The `:prod` / `:stag` floating tags follow main / develop pushes, never a release tag."""
        for wf in IMAGE_WORKFLOWS:
            text = wf.read_text()
            with self.subTest(workflow=wf.name):
                for line in text.splitlines():
                    if "refs/tags/v" in line:
                        assert ":prod" not in line, f"{wf.name}: {line.strip()}"
                        assert ":stag" not in line, f"{wf.name}: {line.strip()}"

    def test_workflow_run_gated_builds_let_the_tag_push_through(self) -> None:
        """A tag push has no workflow_run context, so the success gate must admit event_name push."""
        for wf in IMAGE_WORKFLOWS:
            text = wf.read_text()
            if "workflow_run:" not in text:
                continue
            with self.subTest(workflow=wf.name):
                assert "github.event_name == 'push'" in text, f"{wf.name}: job `if` gate does not admit a tag push"


GUARD_ACTION = WORKFLOWS.parent / "actions" / "release-tag-guard" / "action.yml"
GUARD_USE = "uses: ./.github/actions/release-tag-guard"


class ReleaseTagGuard(unittest.TestCase):
    """A tag push is not gated by branch protection, so the workflows gate themselves.

    Anyone with write access can push a v* tag at any commit; without the guard every image
    workflow would publish release-looking :v<X.Y.Z> images from unreleased code. The composite
    action refuses a STABLE v<X.Y.Z> whose commit is not on main and lets pre-release (-rc.N)
    tags through — the release-candidate path.
    """

    def test_guard_action_asks_main_for_ancestry_and_exempts_pre_releases(self) -> None:
        text = GUARD_ACTION.read_text()
        assert "compare/main..." in text, "the guard must ask the API whether the tag commit is on main"
        assert "behind|identical" in text, "an ancestor of main reads as compare status behind (or identical)"
        assert "exit 1" in text, "a stable tag off main must fail the build, not warn"
        # Stable tags are exactly v<major>.<minor>.<patch>; anything with a suffix is a pre-release and passes.
        assert r"^v[0-9]+\.[0-9]+\.[0-9]+$" in text, "the stable-tag pattern must be anchored on both ends"
        assert "pre-release" in text

    def test_every_image_workflow_runs_the_guard_after_checkout_and_before_building(self) -> None:
        for wf in IMAGE_WORKFLOWS:
            text = wf.read_text()
            with self.subTest(workflow=wf.name):
                guard_at = text.find(GUARD_USE)
                assert guard_at != -1, f"{wf.name}: does not run {GUARD_USE}"
                checkout_at = text.find("uses: actions/checkout@")
                assert 0 <= checkout_at < guard_at, f"{wf.name}: the guard must run after the checkout it needs"
                # Command lines only (a comment may mention `docker build` well before the step).
                build_cmd = re.compile(r"^\s+docker (build|push)\b", re.MULTILINE)
                first_build = build_cmd.search(text)
                assert first_build, f"{wf.name}: no docker build/push command found"
                assert guard_at < first_build.start(), f"{wf.name}: the guard must run before any docker build/push"


RELEASE_WORKFLOW = WORKFLOWS / "release.yml"


class ReleaseDispatchesTheBuilds(unittest.TestCase):
    """release.yml must dispatch every image workflow at the tag it creates.

    The tag is pushed with the workflow's own GITHUB_TOKEN, and GitHub starts no workflow for an
    event created that way (only workflow_dispatch / repository_dispatch are exempt) — so the
    `push.tags` trigger the image workflows carry never fires on a real release. A hand-pushed
    tag (a release candidate) does fire it, which is exactly why a manual proof would not catch
    a workflow missing from the roster below.
    """

    def test_release_dispatches_exactly_the_publishing_image_workflows(self) -> None:
        text = RELEASE_WORKFLOW.read_text()
        step = text[text.index("Build every image at the release tag") :]
        step = step[: step.index("- name:", 10)] if "- name:" in step[10:] else step
        roster = re.compile(r"^\s+((?:docker_build_|fl-docker-build-)[A-Za-z0-9_-]+\.yml)", re.MULTILINE)
        dispatched = set(roster.findall(step))
        expected = {wf.name for wf in IMAGE_WORKFLOWS}
        assert dispatched == expected, f"missing={sorted(expected - dispatched)} extra={sorted(dispatched - expected)}"
        assert 'gh workflow run "$wf" --ref "$TAG"' in step

    def test_release_job_may_dispatch_workflows(self) -> None:
        text = RELEASE_WORKFLOW.read_text()
        assert re.search(r"^\s+actions: write", text, re.MULTILINE), "release.yml needs actions: write to dispatch"

    def test_image_workflows_publish_the_tag_for_a_dispatched_run_too(self) -> None:
        """The :v<X.Y.Z> branch must key on the ref alone — a dispatched run's event is not `push`."""
        for wf in IMAGE_WORKFLOWS:
            text = wf.read_text()
            with self.subTest(workflow=wf.name):
                assert 'GH_EVENT_NAME" == "push" && "$GH_REF" == refs/tags/v' not in text, (
                    f"{wf.name}: the release-tag branch is gated on event_name == push, so release.yml's dispatch would"
                    " publish a sanitised branch-name tag instead of :v<X.Y.Z>"
                )
                assert "workflow_dispatch" in text, f"{wf.name}: release.yml cannot dispatch it without the trigger"


if __name__ == "__main__":
    unittest.main()
