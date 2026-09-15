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


if __name__ == "__main__":
    unittest.main()
