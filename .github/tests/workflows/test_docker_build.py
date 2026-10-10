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
import subprocess
import sys
import tempfile
import textwrap
import unittest
from fnmatch import fnmatchcase
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from image_workflows import (  # noqa: E402
    DOCKER_BUILD_WORKFLOWS,
    GITHUB_DIR,
    WORKFLOWS,
    ReleaseTagContract,
    code_text,
    step_block,
)


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


class XnatBuildTriggers(unittest.TestCase):
    """An image-context edit rebuilds that image; the dcm2niix command is baked into xnat-web."""

    workflows = ("web", "db", "nginx")

    def test_changed_paths_select_the_required_images(self) -> None:
        cases: tuple[tuple[list[str], set[str]], ...] = (
            (["trust/xnat/postgres/Dockerfile"], {"db", "web"}),
            (["trust/xnat/nginx/nginx.conf"], {"nginx", "web"}),
            (["trust/xnat/xnat/Dockerfile"], {"web"}),
            (["trust/xnat/.env"], {"web"}),
            (["trust/xnat/dcm2niix/Dockerfile"], set()),
            (
                ["trust/xnat/dcm2niix/Dockerfile", "trust/xnat/xnat/config/dcm2niix_command.json"],
                {"web"},
            ),
            (["flip-api/src/flip_api/main.py"], set()),
        )
        for changed_paths, expected in cases:
            selected = set()
            for image in self.workflows:
                text = code_text(WORKFLOWS / f"docker_build_xnat_{image}.yml")
                paths = re.search(r"\n    paths:\n((?:      - .*\n)+)", text)
                assert paths, image
                patterns = re.findall(r"      - [\"\']([^\"\']+)[\"\']", paths[1])
                # GitHub evaluates positive/negative path filters in order; any included
                # changed file starts the workflow. These globs use only * and **.
                for changed_path in changed_paths:
                    included = False
                    for pattern in patterns:
                        if fnmatchcase(changed_path, pattern.removeprefix("!")):
                            included = not pattern.startswith("!")
                    if included:
                        selected.add(image)
            with self.subTest(changed_paths=changed_paths):
                assert selected == expected, f"selected={selected}, expected={expected}"

    def test_own_workflow_edits_still_trigger_each_build(self) -> None:
        for image in self.workflows:
            name = f"docker_build_xnat_{image}.yml"
            text = code_text(WORKFLOWS / name)
            with self.subTest(image=image):
                assert f'      - ".github/workflows/{name}"' in text, image

    def test_push_triggered_builds_do_not_read_workflow_run_context(self) -> None:
        for image in self.workflows:
            text = code_text(WORKFLOWS / f"docker_build_xnat_{image}.yml")
            with self.subTest(image=image):
                assert "workflow_run" not in text, image
                assert "GH_WR_" not in text, image
                assert 'if [[ "$GH_EVENT_NAME" == "push" ]]; then' in text, image

    def test_tag_generation_preserves_push_and_dispatch_behavior(self) -> None:
        sha = "1234567890abcdef"  # pragma: allowlist secret (synthetic commit ID)
        cases = (
            ("push", "main", [sha, "sha-1234567", "main", "prod"]),
            ("push", "develop", [sha, "sha-1234567", "develop", "stag"]),
            ("workflow_dispatch", "feature/foo", [sha, "sha-1234567", "feature-foo"]),
            ("push", "v1.2.3", ["v1.2.3"]),
            ("workflow_dispatch", "v1.2.3", ["v1.2.3"]),
        )
        for image in self.workflows:
            step = step_block(code_text(WORKFLOWS / f"docker_build_xnat_{image}.yml"), "Determine tags")
            script = textwrap.dedent(step.split("        run: |\n", 1)[1])
            for expression, variable in (
                ("github.sha", "GH_SHA"),
                ("env.REGISTRY", "REGISTRY"),
                ("env.IMAGE_NAME", "IMAGE_NAME"),
            ):
                script = script.replace("${{ " + expression + " }}", "${" + variable + "}")
            for event, ref_name, expected_tags in cases:
                with self.subTest(image=image, event=event, ref=ref_name), tempfile.TemporaryDirectory() as directory:
                    output = Path(directory) / "output"
                    env = {
                        "GH_EVENT_NAME": event,
                        "GH_REF_NAME": ref_name,
                        "GH_REF": f"refs/{'tags' if ref_name.startswith('v') else 'heads'}/{ref_name}",
                        "GH_SHA": sha,
                        "REGISTRY": "ghcr.io",
                        "IMAGE_NAME": f"londonaicentre/xnat-{image}",
                        "GITHUB_OUTPUT": str(output),
                    }
                    result = subprocess.run(["bash", "-e"], input=script, env=env, capture_output=True, text=True)
                    assert result.returncode == 0, result.stderr
                    expected = ",".join(f"ghcr.io/londonaicentre/xnat-{image}:{tag}" for tag in expected_tags)
                    assert output.read_text().strip() == f"tags={expected}", output.read_text()


if __name__ == "__main__":
    unittest.main()
