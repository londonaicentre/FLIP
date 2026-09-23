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
"""The ``docker_build_*.yml`` image workflows build and push ``:v<X.Y.Z>`` at a release tag (FLIP#1204).

A release is one tag across the whole stack, so every image is rebuilt at the release commit,
changed or not. Read as text, so a workflow that drops the trigger fails here rather than at the
next release. ``docker_build_flip_ui.yml`` publishes nothing and is not on the roster.

Usage:
    python3 .github/tests/workflows/test_docker_build.py
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from image_workflows import DOCKER_BUILD_WORKFLOWS, GITHUB_DIR, WORKFLOWS, ReleaseTagContract, code_text  # noqa: E402


class DockerBuildReleaseTags(ReleaseTagContract, unittest.TestCase):
    workflows = DOCKER_BUILD_WORKFLOWS

    def test_roster_is_not_empty(self) -> None:
        assert len(self.workflows) >= 10, [p.name for p in self.workflows]

    def test_a_release_tag_run_publishes_only_the_release_tag(self) -> None:
        """The sha tags belong to the branch build of the same commit: a tag run pushing them too would
        swap the image under a sha-pinned hub, with another FLIP_RELEASE baked in."""
        release_branch = re.compile(
            r'if \[\[ "\$GH_REF" == refs/tags/v\* \]\]; then\n(?P<body>(?:[^\n]*\n)*?)\s*else\n'
        )
        for wf in self.workflows:
            text = code_text(wf)
            with self.subTest(workflow=wf.name):
                branch = release_branch.search(text)
                assert branch, f"{wf.name}: no refs/tags/v* branch in the tag step"
                assigns = [line.strip() for line in branch["body"].splitlines() if line.strip().startswith("TAGS=")]
                assert len(assigns) == 1, f"{wf.name}: {assigns}"
                assert not assigns[0].startswith('TAGS="${TAGS}'), (
                    f"{wf.name}: the release branch appends to {assigns[0]}"
                )
                assert assigns[0].endswith(':${GH_REF_NAME}"'), f"{wf.name}: {assigns[0]}"


# The four service images whose /health reports FLIP_RELEASE; the rest publish at the tag without it.
API_WORKFLOWS = {
    "docker_build_flip_api.yml": "flip-api/Dockerfile",
    "docker_build_trust_trust_api.yml": "trust/trust-api/Dockerfile",
    "docker_build_trust_imaging_api.yml": "trust/imaging-api/Dockerfile",
    "docker_build_trust_data_access_api.yml": "trust/data-access-api/Dockerfile",
}


class FlipReleaseBuildArg(unittest.TestCase):
    """If a service image loses FLIP_RELEASE, its /health falls back to the pyproject number and the
    Connection Status drift pill stops flagging it — nothing else would notice."""

    def test_the_api_workflows_pass_the_computed_release_to_the_build(self) -> None:
        for name in API_WORKFLOWS:
            text = code_text(WORKFLOWS / name)
            with self.subTest(workflow=name):
                assert "FLIP_RELEASE: ${{ steps.tags.outputs.release }}" in text, name
                assert '--build-arg FLIP_RELEASE="$FLIP_RELEASE"' in text, name
                assert 'echo "release=${RELEASE}" >> $GITHUB_OUTPUT' in text, name

    def test_the_api_dockerfiles_bake_it(self) -> None:
        for dockerfile in API_WORKFLOWS.values():
            text = (GITHUB_DIR.parent / dockerfile).read_text()
            with self.subTest(dockerfile=dockerfile):
                assert 'ARG FLIP_RELEASE=""' in text, dockerfile
                assert "ENV FLIP_RELEASE=${FLIP_RELEASE}" in text, dockerfile


if __name__ == "__main__":
    unittest.main()
