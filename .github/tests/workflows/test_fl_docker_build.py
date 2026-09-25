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
"""The ``fl-docker-build-*.yml`` workflows push every FL image at a release tag, and only there (FLIP#1204).

Usage:
    python3 .github/tests/workflows/test_fl_docker_build.py
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from image_workflows import FL_DOCKER_BUILD_WORKFLOWS, ReleaseTagContract, code_text, step_block  # noqa: E402

IMAGES = ("BASE", "SERVER", "CLIENT", "API")


class FlDockerBuildReleaseTags(ReleaseTagContract, unittest.TestCase):
    """The "Determine image tags" step is the one list each image is pushed at; a release run's list is the
    release tag alone (the sha tags belong to the branch build of the same commit and stay immutable)."""

    workflows = FL_DOCKER_BUILD_WORKFLOWS

    def test_roster_is_not_empty(self) -> None:
        assert len(self.workflows) == 2, [p.name for p in self.workflows]

    def test_a_release_run_lists_only_the_release_tag(self) -> None:
        for wf in self.workflows:
            step = step_block(code_text(wf), "Determine image tags")
            branch = re.search(
                r'if \[\[ "\$\{\{ github\.ref \}\}" == refs/tags/v\* \]\]; then\n(.*?)\n\s*fi\n', step, re.S
            )
            with self.subTest(workflow=wf.name):
                assert branch, f"{wf.name}: no refs/tags/v* branch in the tag step"
                assigned = [line.strip() for line in branch[1].splitlines()]
                expected = [f'{i}_TAGS="${{REGISTRY}}/${{ORG}}/${{{i}_IMAGE}}:${{GITHUB_REF_NAME}}"' for i in IMAGES]
                assert assigned == expected, f"{wf.name}: {assigned}"

    def test_every_image_is_pushed_from_its_tag_list_and_nothing_else_is(self) -> None:
        for wf in self.workflows:
            text = code_text(wf)
            with self.subTest(workflow=wf.name):
                assert re.findall(r"^\s+docker push (.*)$", text, re.M) == ['"$tag"'] * 4, f"{wf.name}: stray pushes"
            for image in IMAGES:
                with self.subTest(workflow=wf.name, image=image):
                    assert f"TAGS: ${{{{ steps.tags.outputs.{image.lower()}_tags }}}}" in text, image
                    assert f'docker tag "$REGISTRY/$ORG/${image}_IMAGE:${{{{ github.sha }}}}" "$tag"' in text, image


if __name__ == "__main__":
    unittest.main()
