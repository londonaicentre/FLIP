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
"""terraform_apply.yml waits for an image group only while its build runs for the commit (FLIP#1811).

image-wait-budgets.sh asks GitHub for one workflow's push runs per group. These tests hold that
workflow to the one that really builds the image the resolver probes, and its push filter to what
the image is built from, so no path list has to be kept in step with the builds.

Usage:
    python3 .github/tests/workflows/test_terraform_image_wait.py
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from image_workflows import GITHUB_DIR, WORKFLOWS, code_text, step_block  # noqa: E402

REPO = GITHUB_DIR.parent
SCRIPTS = REPO / "deploy" / "providers" / "AWS" / "scripts"
BUDGETS = SCRIPTS / "image-wait-budgets.sh"
RESOLVER = SCRIPTS / "resolve-image-tags.sh"
BACKENDS = ("nvflare", "flower")

# A queued run for the workflow named in $ACTIVE, none for any other; logs every workflow asked about.
STUB_GH = """#!/usr/bin/env bash
wf="${2#*/actions/workflows/}"
wf="${wf%%/*}"
echo "$wf" >>"$ASKED"
if [[ "$wf" == "$ACTIVE" ]]; then
    echo '{"workflow_runs":[{"status":"queued","conclusion":null}]}'
else
    echo '{"workflow_runs":[]}'
fi
"""


def push_block(text: str) -> str:
    block = re.search(r"^  push:\n((?:    .*\n)+)", text, re.M)
    assert block, "no push trigger"
    return block[1]


def push_paths(text: str) -> list[str]:
    paths = re.search(r"^    paths:\n((?:      - .*\n)+)", push_block(text), re.M)
    assert paths, "the push trigger has no paths filter"
    return [p.strip().strip("\"'") for p in re.findall(r"- (.*)", paths[1])]


def covered(path: str, patterns: list[str]) -> bool:
    return any(p == path or (p.endswith("/**") and f"{path}/".startswith(p[:-2])) for p in patterns)


def copy_sources(dockerfile: Path) -> set[str]:
    """Repository paths a Dockerfile built from the repo root COPYs in (named contexts excluded)."""
    sources = set()
    for line in dockerfile.read_text().splitlines():
        copy = re.match(r"\s*(?:COPY|ADD)\s+(.*)", line)
        if copy and "--from" not in copy[1]:
            words = [w for w in copy[1].split() if not w.startswith("--")]
            sources |= {w.rstrip("/") for w in words[:-1]}
    return sources


def build_inputs(text: str) -> set[str]:
    """What the workflow's `docker build` commands read: Dockerfile dirs, contexts, named contexts."""
    workdir = re.search(r"working-directory: \./(\S+)", text)
    base = workdir[1] if workdir else ""
    inputs = set()
    for command in re.findall(r"docker build ((?:[^\n]*\\\n)*[^\n]*)", text):
        command = command.replace("\\\n", " ")
        context = os.path.normpath(os.path.join(base, command.split()[-1]))
        dockerfile = re.search(r"-f (\S+)", command)
        dockerfile_path = dockerfile[1] if dockerfile else os.path.join(context, "Dockerfile")
        inputs.add(os.path.dirname(dockerfile_path))
        for named in re.findall(r"--build-context \w+=(\S+)", command):
            inputs.add(os.path.normpath(os.path.join(base, named)))
        if context == ".":
            inputs |= copy_sources(REPO / dockerfile_path)
        else:
            inputs.add(context)
    assert inputs, "no docker build found"
    return inputs


def hub_publisher() -> Path:
    """The workflow that pushes the hub image the resolver probes."""
    image = re.search(r'^resolve "hub images" "([^"]+)"', RESOLVER.read_text(), re.M)[1]
    found = [
        wf
        for wf in sorted(WORKFLOWS.glob("*.yml"))
        if re.search(rf"^\s+IMAGE_NAME: londonaicentre/{image}$", code_text(wf), re.M)
    ]
    assert len(found) == 1, [wf.name for wf in found]
    return found[0]


def resolver_fl_images(backend: str) -> dict[str, str]:
    arm = re.search(rf"^\s+{backend}\)\n((?:\s+FL_\w+_IMAGE=\"[^\"]*\"\n)+)", RESOLVER.read_text(), re.M)
    assert arm, f"resolver has no {backend} arm"
    return dict(re.findall(r"FL_(\w+)_IMAGE=\"([^\"]*)\"", arm[1]))


class TheWaitFollowsTheBuilds(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp())
        (cls.tmp / "gh").write_text(STUB_GH)
        (cls.tmp / "gh").chmod(0o755)
        # Which workflow decides each group: make each workflow the script asks about run in turn.
        cls.groups = {}
        for backend in BACKENDS:
            _, asked = cls.budgets(backend, "")
            groups: dict[str, list[str]] = {}
            for workflow in asked:
                out, _ = cls.budgets(backend, workflow)
                for group in (g for g, seconds in out.items() if seconds != "0"):
                    groups.setdefault(group, []).append(workflow)
            cls.groups[backend] = groups

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def budgets(cls, backend: str, active: str) -> tuple[dict[str, str], list[str]]:
        asked = cls.tmp / "asked"
        asked.write_text("")
        result = subprocess.run(
            ["bash", str(BUDGETS)],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
            env={
                **os.environ,
                "PATH": f"{cls.tmp}{os.pathsep}{os.environ['PATH']}",
                "ASKED": str(asked),
                "ACTIVE": active,
                "GITHUB_REPOSITORY": "londonaicentre/FLIP",
                "GITHUB_SHA": "a" * 40,
                "FL_BACKEND": backend,
                "GHCR_WAIT_SECONDS": "900",
                "RELEASE_TAG": "",
            },
        )
        return dict(line.split("=", 1) for line in result.stdout.splitlines()), asked.read_text().split()

    def workflow(self, backend: str, group: str) -> Path:
        return WORKFLOWS / self.groups[backend][group][0]

    def test_each_group_follows_exactly_one_workflow(self) -> None:
        for backend in BACKENDS:
            with self.subTest(backend=backend):
                groups = self.groups[backend]
                assert sorted(groups) == ["fl", "hub"], groups
                assert all(len(workflows) == 1 for workflows in groups.values()), groups

    def test_each_workflow_runs_on_a_push_to_develop_and_main(self) -> None:
        """The script asks for event=push runs; a workflow that never has one would never be waited for."""
        for backend in BACKENDS:
            for group in ("hub", "fl"):
                wf = self.workflow(backend, group)
                with self.subTest(workflow=wf.name):
                    assert wf.is_file(), f"{wf.name} does not exist"
                    branches = re.search(r"^    branches: \[(.*)\]", push_block(code_text(wf)), re.M)
                    assert branches, f"{wf.name}: push has no branches"
                    assert {"develop", "main"} <= {b.strip() for b in branches[1].split(",")}, branches[1]

    def test_the_hub_workflow_triggers_the_flip_api_publish(self) -> None:
        publisher = code_text(hub_publisher())
        assert ":sha-${SHA:0:7}" in publisher, "the publisher no longer pushes sha-<short7>"
        for backend in BACKENDS:
            wf = self.workflow(backend, "hub")
            name = re.search(r"^name: (.+)$", code_text(wf), re.M)[1].strip("\"'")
            triggers = re.search(r"^  workflow_run:\n    workflows: \[(.*)\]", publisher, re.M)
            with self.subTest(backend=backend):
                assert triggers, f"{hub_publisher().name} is not triggered by a workflow_run"
                assert [t.strip().strip("\"'") for t in triggers[1].split(",")] == [name], triggers[1]

    def test_the_fl_workflow_publishes_the_images_the_resolver_probes(self) -> None:
        for backend in BACKENDS:
            text = code_text(self.workflow(backend, "fl"))
            with self.subTest(backend=backend):
                assert ":sha-${SHORT_SHA}" in text, "no sha-<short7> tag"
                for role, image in resolver_fl_images(backend).items():
                    if image:
                        assert re.search(rf"^\s+{role}_IMAGE: {image}$", text, re.M), f"{role}_IMAGE: {image}"

    def test_each_workflow_fires_on_what_its_image_is_built_from(self) -> None:
        """A change to a path the image is built from must start a build, or there is nothing to wait for."""
        for backend in BACKENDS:
            for group in ("hub", "fl"):
                wf = self.workflow(backend, group)
                built_by = hub_publisher() if group == "hub" else wf
                patterns = push_paths(code_text(wf))
                missing = sorted(p for p in build_inputs(code_text(built_by)) if not covered(p, patterns))
                with self.subTest(workflow=wf.name):
                    assert not missing, f"{wf.name} does not fire on {missing}"


class TerraformApplyUsesTheBudgets(unittest.TestCase):
    def setUp(self) -> None:
        self.text = code_text(WORKFLOWS / "terraform_apply.yml")

    def test_the_wait_step_asks_the_script_not_a_diff(self) -> None:
        step = step_block(self.text, "Decide how long to wait for this commit's images")
        assert 'run: deploy/providers/AWS/scripts/image-wait-budgets.sh >> "$GITHUB_OUTPUT"' in step
        assert "FL_BACKEND: ${{ vars.FL_BACKEND }}" in step
        assert "GHCR_WAIT_SECONDS: ${{ vars.GHCR_WAIT_SECONDS || 900 }}" in step
        # A release's images come from tag-dispatched builds; the script does not ask about it.
        assert "RELEASE_TAG: ${{ inputs.release_tag }}" in step
        assert "GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}" in step
        assert "git diff" not in step
        assert "event.before" not in step

    def test_the_resolver_gets_one_budget_per_group(self) -> None:
        step = step_block(self.text, "Resolve the image tags to pin")
        assert "GHCR_WAIT_SECONDS_HUB: ${{ steps.wait.outputs.hub }}" in step
        assert "GHCR_WAIT_SECONDS_FL: ${{ steps.wait.outputs.fl }}" in step
        assert "GHCR_WAIT_SECONDS: ${{ vars.GHCR_WAIT_SECONDS || 900 }}" in step
        assert "FL_BACKEND: ${{ vars.FL_BACKEND }}" in step


if __name__ == "__main__":
    unittest.main()
