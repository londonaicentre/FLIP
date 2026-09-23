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
"""The image-publishing workflows the release tests hold to one contract (FLIP#1204).

A release is one `v<X.Y.Z>` tag across the whole stack, so every workflow that publishes an image
to GHCR must build at that tag. flip-ui's build is a smoke test that publishes nothing, so it is
not on the roster.
"""

from __future__ import annotations

import re
from pathlib import Path

GITHUB_DIR = Path(__file__).resolve().parents[1]
WORKFLOWS = GITHUB_DIR / "workflows"

IMAGE_WORKFLOWS = sorted(
    p
    for p in WORKFLOWS.glob("*.yml")
    if (p.name.startswith("docker_build_") and p.name != "docker_build_flip_ui.yml")
    or p.name.startswith("fl-docker-build-")
)
DOCKER_BUILD_WORKFLOWS = [p for p in IMAGE_WORKFLOWS if p.name.startswith("docker_build_")]
FL_DOCKER_BUILD_WORKFLOWS = [p for p in IMAGE_WORKFLOWS if p.name.startswith("fl-docker-build-")]


def code_text(path: Path) -> str:
    """The file without its comment lines, so prose that mentions a construct never satisfies a check."""
    return "\n".join(line for line in path.read_text().splitlines() if not line.lstrip().startswith("#")) + "\n"


def step_block(text: str, name: str) -> str:
    """One workflow step, from its ``- name: <name>`` line to the next step."""
    return text[text.index(f"- name: {name}") :].split("- name:", 2)[1]


# `push:` block carrying a `tags:` list (flow or block style) whose entry is the platform
# release glob. The flip-utils train tags `flip-utils-v*` and must NOT trigger image builds,
# so the glob is asserted exactly rather than loosely.
_GLOB = r"['\"]v\*\.\*\.\*['\"]"
TAG_TRIGGER = re.compile(
    r"\n {2}push:\s*\n(?:[^\n]*\n)*? {4}tags:\s*(?:\[\s*" + _GLOB + r"\s*\]|\n\s+- " + _GLOB + r")"
)


class ReleaseTagContract:
    """The release-tag contract every image workflow in ``workflows`` must meet; mixed into a TestCase.

    A real release reaches these workflows as a ``workflow_dispatch`` at the tag ref (release.yml),
    a release candidate as a hand-pushed tag, so both paths are pinned here.
    """

    workflows: list[Path] = []

    def test_every_image_workflow_triggers_on_release_tags(self) -> None:
        for wf in self.workflows:
            text = code_text(wf)
            with self.subTest(workflow=wf.name):
                # Plain asserts with a short message: an assertRegex/assertIn failure would dump
                # the whole workflow file, burying the one line that matters.
                assert TAG_TRIGGER.search(text), f"{wf.name}: no `push.tags: ['v*.*.*']` trigger"
                assert "refs/tags/v" in text, f"{wf.name}: the tag step never branches on refs/tags/v*"

    def test_release_tag_never_moves_prod_or_stag(self) -> None:
        """The `:prod` / `:stag` floating tags follow main / develop pushes, never a release tag."""
        for wf in self.workflows:
            text = code_text(wf)
            with self.subTest(workflow=wf.name):
                for line in text.splitlines():
                    if "refs/tags/v" in line:
                        assert ":prod" not in line, f"{wf.name}: {line.strip()}"
                        assert ":stag" not in line, f"{wf.name}: {line.strip()}"

    def test_workflow_run_gated_builds_let_the_tag_push_through(self) -> None:
        """A tag push has no workflow_run context, so the success gate must admit event_name push."""
        for wf in self.workflows:
            text = code_text(wf)
            if "workflow_run:" not in text:
                continue
            with self.subTest(workflow=wf.name):
                assert "github.event_name == 'push'" in text, f"{wf.name}: job `if` gate does not admit a tag push"

    def test_image_workflows_publish_the_tag_for_a_dispatched_run_too(self) -> None:
        """The :v<X.Y.Z> branch must key on the ref alone — a dispatched run's event is not `push`."""
        for wf in self.workflows:
            text = code_text(wf)
            with self.subTest(workflow=wf.name):
                assert 'GH_EVENT_NAME" == "push" && "$GH_REF" == refs/tags/v' not in text, (
                    f"{wf.name}: the release-tag branch is gated on event_name == push, so release.yml's dispatch would"
                    " publish a sanitised branch-name tag instead of :v<X.Y.Z>"
                )
                assert "workflow_dispatch" in text, f"{wf.name}: release.yml cannot dispatch it without the trigger"
