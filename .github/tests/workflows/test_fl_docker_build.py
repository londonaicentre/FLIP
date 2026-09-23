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
"""The ``fl-docker-build-*.yml`` workflows tag and push every FL image at a release tag (FLIP#1204).

Usage:
    python3 .github/tests/workflows/test_fl_docker_build.py
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from image_workflows import FL_DOCKER_BUILD_WORKFLOWS, ReleaseTagContract  # noqa: E402


class FlDockerBuildReleaseTags(ReleaseTagContract, unittest.TestCase):
    workflows = FL_DOCKER_BUILD_WORKFLOWS


class HandRolledPushes(unittest.TestCase):
    """The FL workflows build with `docker build -t` and push with explicit `docker push` lines.

    Their "Determine image tags" step computes a tag list that no later step reads, so the
    `:v<X.Y.Z>` it names was never tagged or pushed — found by the v0.6.1-rc.1204 candidate,
    which published every service image at the tag and none of the eight FL images. Every image
    pushed at `sha-$SHORT_SHA` must therefore also be tagged and pushed at the release ref name.
    """

    FL_WORKFLOWS = FL_DOCKER_BUILD_WORKFLOWS
    SHA_PUSH = re.compile(r"^\s+docker push (\$REGISTRY/\$ORG/\$\w+):sha-\$SHORT_SHA\s*$", re.MULTILINE)

    def test_roster_is_not_empty(self) -> None:
        assert len(self.FL_WORKFLOWS) == 2, [p.name for p in self.FL_WORKFLOWS]

    def test_every_sha_pushed_image_is_tagged_and_pushed_at_the_release(self) -> None:
        for wf in self.FL_WORKFLOWS:
            text = wf.read_text()
            images = self.SHA_PUSH.findall(text)
            with self.subTest(workflow=wf.name):
                assert len(images) == 4, f"{wf.name}: expected 4 sha pushes, found {images}"
            for image in images:
                with self.subTest(workflow=wf.name, image=image):
                    img = re.escape(image)
                    tag = re.compile(r"^\s+docker tag [^\n]+ " + img + r":\$\{GITHUB_REF_NAME\}\s*$", re.MULTILINE)
                    push = re.compile(r"^\s+docker push " + img + r":\$\{GITHUB_REF_NAME\}\s*$", re.MULTILINE)
                    assert tag.search(text), f"{wf.name}: {image} is never tagged :<release>"
                    assert push.search(text), f"{wf.name}: {image} is never pushed :<release>"

    def test_release_push_is_gated_on_a_v_tag_ref(self) -> None:
        """A branch run must not push `:<branch>` — the release lines sit under a refs/tags/v* test."""
        for wf in self.FL_WORKFLOWS:
            text = wf.read_text()
            with self.subTest(workflow=wf.name):
                gated = re.findall(
                    r'elif \[\[ "\$\{\{ github\.ref \}\}" == refs/tags/v\* \]\]; then\n\s+docker (?:tag|push) [^\n]*'
                    r":\$\{GITHUB_REF_NAME\}",
                    text,
                )
                ungated = re.findall(r"^\s+docker (?:tag|push) [^\n]*:\$\{GITHUB_REF_NAME\}", text, re.MULTILINE)
                assert len(gated) == len(ungated) == 8, f"{wf.name}: {len(gated)} gated of {len(ungated)}"


if __name__ == "__main__":
    unittest.main()
