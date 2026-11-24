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

import posixpath
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))

from image_workflows import (  # noqa: E402
    DOCKER_BUILD_WORKFLOWS,
    GITHUB_DIR,
    WORKFLOWS,
    ReleaseTagContract,
    code_text,
    covered,
    push_block,
    push_paths,
    step_block,
)
from pr_paths_changed import glob_to_regex  # noqa: E402


class DockerBuildReleaseTags(ReleaseTagContract, unittest.TestCase):
    workflows = DOCKER_BUILD_WORKFLOWS

    def test_roster_is_not_empty(self) -> None:
        assert len(self.workflows) >= 10, [p.name for p in self.workflows]

    def test_a_gated_build_runs_once_per_commit(self) -> None:
        """The test suite also runs for the develop→main PR, with head branch develop: without the event
        clause that run builds the commit again and replaces its immutable sha tag (FLIP#2020)."""
        gated = [p for p in self.workflows if "\n  workflow_run:\n" in code_text(p)]
        assert len(gated) >= 5, [p.name for p in gated]
        for path in gated:
            with self.subTest(workflow=path.name):
                conditions = re.findall(r"^    if: >-\n((?:      .*\n)+)", code_text(path), re.MULTILINE)
                assert conditions, path.name
                for condition in conditions:
                    assert "github.event.workflow_run.event == 'push'" in condition, condition

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
    """The XNAT images publish only after "Trust - XNAT CI" passes (FLIP#880), so its push filter is
    their path filter: an input it misses would never be rebuilt."""

    workflows = [p for p in DOCKER_BUILD_WORKFLOWS if p.name.startswith("docker_build_xnat_")]
    suite = WORKFLOWS / "test_trust_xnat.yml"

    def test_builds_are_gated_on_the_xnat_suite(self) -> None:
        name = re.search(r"^name: (.+)$", code_text(self.suite), re.M)
        assert name, self.suite.name
        assert len(self.workflows) >= 5, [wf.name for wf in self.workflows]
        for wf in self.workflows:
            text = code_text(wf)
            with self.subTest(workflow=wf.name):
                assert f'workflows: ["{name[1]}"]' in text, wf.name
                assert "github.event.workflow_run.conclusion == 'success'" in text, wf.name
                assert "branches:" not in push_block(text), f"{wf.name}: a branch push still publishes ungated"

    def test_every_image_input_triggers_the_suite(self) -> None:
        """Inputs are read off each build: its own file, its context, and every ``../`` path it reaches."""
        patterns = [glob_to_regex(p) for p in push_paths(code_text(self.suite))]
        for wf in self.workflows:
            text = code_text(wf)
            context = re.search(r"working-directory: \./(\S+)", text)
            assert context, wf.name
            inputs = {f".github/workflows/{wf.name}", f"{context[1]}/Dockerfile"}
            inputs |= {posixpath.normpath(f"{context[1]}/{ref}") for ref in re.findall(r"\.\./[\w./-]+", text)}
            for changed_path in sorted(inputs):
                with self.subTest(workflow=wf.name, changed_path=changed_path):
                    assert (GITHUB_DIR.parent / changed_path).is_file(), changed_path
                    assert any(p.match(changed_path) for p in patterns), changed_path

    def test_every_build_context_is_covered_whole(self) -> None:
        """Naming only each Dockerfile would keep the test above green while the rest of the context went unbuilt."""
        paths = push_paths(code_text(self.suite))
        for wf in self.workflows:
            context = re.search(r"working-directory: \./(\S+)", code_text(wf))
            assert context, wf.name
            with self.subTest(workflow=wf.name):
                assert covered(context[1], [p for p in paths if p.endswith("/**")]), context[1]

    def test_published_base_images_match_the_local_build(self) -> None:
        """CI builds from the workflow's build args, a local build and the upgrade verbs read .env.

        The xnat-db major upgrade (FLIP#1812) decides from .env whether to dump the old cluster, so
        a workflow publishing another PostgreSQL major would skip the dump the image then needs.
        """
        env = (GITHUB_DIR.parent / "trust/xnat/.env").read_text()
        for image, arg in (("db", "PG_VERSION"), ("nginx", "NGINX_VERSION")):
            local = re.search(rf"^{arg}=(\S+)$", env, re.M)
            published = re.search(rf"--build-arg {arg}=(\S+)", code_text(WORKFLOWS / f"docker_build_xnat_{image}.yml"))
            with self.subTest(image=image):
                assert local, arg
                assert published, image
                assert local[1] == published[1], (local[1], published[1])

    def test_tags_name_the_tested_commit(self) -> None:
        """Under workflow_run, github.sha is the default branch's head, not the commit built."""
        for wf in self.workflows:
            tags = step_block(code_text(wf), "Determine tags")
            with self.subTest(workflow=wf.name):
                assert 'SHA="$GH_WR_SHA"' in tags, wf.name
                assert 'REF_NAME="$GH_WR_BRANCH"' in tags, wf.name
                assert "${{ github.sha }}" not in tags.split("run: |", 1)[1], wf.name

    def test_tool_version_tags_move_only_with_their_own_source(self) -> None:
        """Every green suite run publishes these images; the tag the sites pin must not follow each one.

        A dispatch still republishes it (a base-image rebuild changes no source), and a failed registry
        lookup must fail the step: read as an absent tag, it would republish the pinned tag.
        """
        for image in ("dcm2niix", "export_mask", "nifti_to_seg"):
            push = step_block(code_text(WORKFLOWS / f"docker_build_xnat_{image}.yml"), "Push Docker image")
            with self.subTest(image=image):
                assert "context-$(git rev-parse HEAD:./)" in push, image
                assert '"$PUBLISHED" == "$CURRENT"' in push, image
                assert 'CURRENT="$(digest "$VERSION_TAG")"' in push, image
                assert '[[ "$GH_EVENT_NAME" != "workflow_dispatch" ]]' in push, image
                assert "GH_EVENT_NAME: ${{ github.event_name }}" in push, image
                assert "2>/dev/null" not in push, image
                assert "return 1" in push, image

    def test_xnat_web_pulls_its_moving_base(self) -> None:
        """The Tomcat base tag moves: without --pull a runner holding an old copy would build on it."""
        build = step_block(code_text(WORKFLOWS / "docker_build_xnat_web.yml"), "Build Docker image")
        assert "docker build --pull " in build, build


if __name__ == "__main__":
    unittest.main()
