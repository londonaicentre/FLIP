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
"""``release.yml`` dispatches every image workflow at the release tag it creates (FLIP#1204).

Usage:
    python3 .github/tests/workflows/test_release.py
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from image_workflows import IMAGE_WORKFLOWS, WORKFLOWS  # noqa: E402

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


if __name__ == "__main__":
    unittest.main()
