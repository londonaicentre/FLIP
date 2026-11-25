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
"""The ``publish-ref-guard`` action keeps a branch build off the tags deployments resolve (FLIP#1743).

Every image workflow can be dispatched at any ref and then pushes `:<sanitised ref name>` with
`packages: write`, so the ref name is the one part of a published tag the dispatcher picks. The
guard refuses the names a deploy resolves — `stag`, `prod`, `latest`, `dev`, `sha-<short7>`,
`v<X.Y.Z>` — and leaves every other branch-named tag alone, which is the documented
branch-pinning workflow.

Usage:
    python3 .github/tests/actions/test_publish_ref_guard.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from image_workflows import GITHUB_DIR, IMAGE_WORKFLOWS  # noqa: E402

GUARD_ACTION = GITHUB_DIR / "actions" / "publish-ref-guard" / "action.yml"
GUARD_USE = "uses: ./.github/actions/publish-ref-guard"
# mirror_images.yml also holds packages: write but mints no ref-derived tag: it copies the
# manifest's third-party images by digest, and every consumer pins <name>:<tag>@<digest>, so the
# digest is the control there (AGENTS.md "A trust node pulls from one registry").
# docker_build_flip_ui.yml publishes no image at all, which is why neither is in IMAGE_WORKFLOWS.


class PublishRefGuardIsWiredIn(unittest.TestCase):
    """Read as text, so a workflow that drops the step fails here rather than at the next dispatch."""

    def test_every_image_workflow_runs_the_guard_after_checkout_and_before_building(self) -> None:
        for wf in IMAGE_WORKFLOWS:
            text = wf.read_text()
            with self.subTest(workflow=wf.name):
                guard_at = text.find(GUARD_USE)
                assert guard_at != -1, f"{wf.name}: does not run {GUARD_USE}"
                checkout_at = text.find("uses: actions/checkout@")
                assert 0 <= checkout_at < guard_at, f"{wf.name}: the guard must run after the checkout it needs"
                # Command lines only (a comment may mention `docker build` well before the step).
                first_build = re.search(r"^\s+docker (build|push)\b", text, re.MULTILINE)
                assert first_build, f"{wf.name}: no docker build/push command found"
                assert guard_at < first_build.start(), f"{wf.name}: the guard must run before any docker build/push"

    def test_the_guard_refuses_rather_than_warns(self) -> None:
        text = GUARD_ACTION.read_text()
        assert "exit 1" in text, "a reserved tag name must fail the build, not warn"
        # The list is a decision about how tags are consumed; keep it documented in the action.
        for consumed in ("stag", "prod", "latest", "dev", "sha-<short7>", "v<X.Y.Z>"):
            assert consumed in text, consumed


def _guard_script() -> str:
    """The action's `run:` script, as text (the runner has no YAML parser to spare)."""
    lines = GUARD_ACTION.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "run: |") + 1
    indent = len(lines[start]) - len(lines[start].lstrip())
    body = []
    for line in lines[start:]:
        if line.strip() and len(line) - len(line.lstrip()) < indent:
            break
        body.append(line[indent:])
    return "\n".join(body) + "\n"


def _run_guard(ref: str, event: str = "workflow_dispatch", workflow_run_branch: str = "") -> tuple[int, str]:
    """Run the guard script for ``ref`` under ``event``. Returns (exit code, output)."""
    with tempfile.TemporaryDirectory() as tmp:
        env = {
            **os.environ,
            "GH_EVENT_NAME": event,
            "GH_REF": ref,
            "GH_REF_NAME": ref.removeprefix("refs/tags/").removeprefix("refs/heads/"),
            "GH_WR_BRANCH": workflow_run_branch,
        }
        result = subprocess.run(
            ["bash", "-e", "-c", _guard_script()], env=env, cwd=tmp, capture_output=True, text=True, timeout=30
        )
        return result.returncode, result.stdout + result.stderr


# Branch names whose published tag a deploy would resolve: the configured floats, docker's
# implicit default, the local dev convention, the immutable short-SHA pin and the release tag.
RESERVED_BRANCHES = ("stag", "prod", "latest", "dev", "sha-2bf07b9", "sha-2bf07b9abcdef", "v1.2.3", "v10.0.1-rc.2")


class PublishRefGuardBehaviour(unittest.TestCase):
    """The guard's script, run for real: which ref may publish a branch-named tag."""

    def test_a_dispatch_from_a_deployment_named_branch_is_refused(self) -> None:
        for branch in RESERVED_BRANCHES:
            with self.subTest(branch=branch):
                code, out = _run_guard(f"refs/heads/{branch}")
                assert code == 1, out
                assert "refusing to publish" in out, out

    def test_case_does_not_launder_a_reserved_name(self) -> None:
        for branch in ("PROD", "Stag", "LATEST"):
            with self.subTest(branch=branch):
                code, out = _run_guard(f"refs/heads/{branch}")
                assert code == 1, out

    def test_sanitisation_does_not_launder_a_reserved_name(self) -> None:
        """`v1.2.3` sanitises to `v1-2-3` and `sha-2bf07b9` survives unchanged: both shapes are
        checked on the raw ref name and on the tag it becomes."""
        for branch in ("v1.2.3", "v1-2-3"):
            with self.subTest(branch=branch):
                code, out = _run_guard(f"refs/heads/{branch}")
                assert code == 1, out

    def test_an_ordinary_branch_still_publishes_its_branch_tag(self) -> None:
        """Pinning a branch-named tag for prod testing is an intended workflow (AGENTS.md)."""
        for branch in ("1743-ci-publish-gates", "feature/sha-tags", "fix/prod-login", "release-v0.16.0"):
            with self.subTest(branch=branch):
                code, out = _run_guard(f"refs/heads/{branch}")
                assert code == 0, out

    def test_main_and_develop_are_not_refused(self) -> None:
        """They are review-gated branches, no deploy resolves `:main` / `:develop`, and dispatching
        a rebuild on them is the documented operator path."""
        for branch in ("main", "develop"):
            with self.subTest(branch=branch):
                assert _run_guard(f"refs/heads/{branch}")[0] == 0
                assert _run_guard("refs/heads/develop", event="workflow_run", workflow_run_branch=branch)[0] == 0

    def test_a_workflow_run_build_is_judged_by_the_branch_that_was_tested(self) -> None:
        """On workflow_run github.ref_name is the default branch, not the built code's branch — the
        tag step uses head_branch, so the guard must too."""
        code, out = _run_guard("refs/heads/develop", event="workflow_run", workflow_run_branch="prod")
        assert code == 1, out

    def test_a_release_tag_run_is_left_to_the_release_tag_guard(self) -> None:
        for tag in ("v1.2.3", "v1.2.3-rc.1"):
            with self.subTest(tag=tag):
                code, out = _run_guard(f"refs/tags/{tag}", event="push")
                assert code == 0, out
                assert "release-tag-guard" in out, out

    def test_an_unknown_ref_name_refuses_rather_than_publishes_an_empty_tag(self) -> None:
        code, out = _run_guard("refs/heads/", event="workflow_run", workflow_run_branch="")
        assert code == 1, out
        assert "empty ref name" in out, out


if __name__ == "__main__":
    unittest.main()
