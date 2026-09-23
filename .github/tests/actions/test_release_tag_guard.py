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
"""The ``release-tag-guard`` action keeps a stable release tag off unreleased code (FLIP#1204).

Usage:
    python3 .github/tests/actions/test_release_tag_guard.py
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

GUARD_ACTION = GITHUB_DIR / "actions" / "release-tag-guard" / "action.yml"
GUARD_USE = "uses: ./.github/actions/release-tag-guard"


class ReleaseTagGuard(unittest.TestCase):
    """A tag push is not gated by branch protection, so the workflows gate themselves.

    Anyone with write access can push a v* tag at any commit; without the guard every image
    workflow would publish release-looking :v<X.Y.Z> images from unreleased code. The composite
    action refuses a STABLE v<X.Y.Z> whose commit is not on main and lets pre-release (-rc.N)
    tags through — the release-candidate path.
    """

    def test_guard_action_asks_main_for_ancestry_and_exempts_pre_releases(self) -> None:
        text = GUARD_ACTION.read_text()
        assert "compare/main..." in text, "the guard must ask the API whether the tag commit is on main"
        assert "behind|identical" in text, "an ancestor of main reads as compare status behind (or identical)"
        assert "exit 1" in text, "a stable tag off main must fail the build, not warn"
        # Stable tags are exactly v<major>.<minor>.<patch>; anything with a suffix is a pre-release and passes.
        assert r"^v[0-9]+\.[0-9]+\.[0-9]+$" in text, "the stable-tag pattern must be anchored on both ends"
        assert "pre-release" in text

    def test_every_image_workflow_runs_the_guard_after_checkout_and_before_building(self) -> None:
        for wf in IMAGE_WORKFLOWS:
            text = wf.read_text()
            with self.subTest(workflow=wf.name):
                guard_at = text.find(GUARD_USE)
                assert guard_at != -1, f"{wf.name}: does not run {GUARD_USE}"
                checkout_at = text.find("uses: actions/checkout@")
                assert 0 <= checkout_at < guard_at, f"{wf.name}: the guard must run after the checkout it needs"
                # Command lines only (a comment may mention `docker build` well before the step).
                build_cmd = re.compile(r"^\s+docker (build|push)\b", re.MULTILINE)
                first_build = build_cmd.search(text)
                assert first_build, f"{wf.name}: no docker build/push command found"
                assert guard_at < first_build.start(), f"{wf.name}: the guard must run before any docker build/push"


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


def _run_guard(ref: str, gh: str | None) -> tuple[int, str, bool]:
    """Run the guard script for ``ref`` with a stub `gh` answering ``gh`` (a compare status, or
    None for an API error). Returns (exit code, output, whether gh was called)."""
    with tempfile.TemporaryDirectory() as tmp:
        stub = Path(tmp) / "gh"
        called = Path(tmp) / "called"
        answer = f"echo {gh}" if gh is not None else "echo 'HTTP 502' >&2; exit 1"
        stub.write_text(f"#!/bin/bash\ntouch {called}\n{answer}\n")
        stub.chmod(0o755)
        env = {
            **os.environ,
            "PATH": f"{tmp}:{os.environ['PATH']}",
            "GH_REPO": "londonaicentre/FLIP",
            "TAG_REF": ref,
            "TAG_NAME": ref.removeprefix("refs/tags/").removeprefix("refs/heads/"),
            "TAG_SHA": "23cf33134b8e2f0c1d2e3f4a5b6c7d8e9f0a1b2c",  # pragma: allowlist secret
        }
        result = subprocess.run(
            ["bash", "-e", "-c", _guard_script()], env=env, capture_output=True, text=True, timeout=30
        )
        return result.returncode, result.stdout + result.stderr, called.exists()


class ReleaseTagGuardBehaviour(unittest.TestCase):
    """The guard's script, run for real: what a stable tag may point at."""

    def test_a_branch_run_is_not_guarded(self) -> None:
        code, out, called = _run_guard("refs/heads/develop", "ahead")
        assert (code, called) == (0, False), out

    def test_a_release_candidate_passes_without_asking_the_api(self) -> None:
        for tag in ("v0.7.0-rc.1", "v1.0.0-rc.12"):
            with self.subTest(tag=tag):
                code, out, called = _run_guard(f"refs/tags/{tag}", "diverged")
                assert (code, called) == (0, False), out

    def test_a_stable_tag_on_main_passes(self) -> None:
        for status in ("behind", "identical"):
            with self.subTest(status=status):
                code, out, called = _run_guard("refs/tags/v0.7.0", status)
                assert (code, called) == (0, True), out

    def test_a_stable_tag_off_main_is_refused(self) -> None:
        for status in ("ahead", "diverged"):
            with self.subTest(status=status):
                code, out, _ = _run_guard("refs/tags/v0.7.0", status)
                assert code == 1, out
                assert "not on main" in out, out

    def test_an_api_error_refuses_rather_than_guesses(self) -> None:
        code, out, _ = _run_guard("refs/tags/v0.7.0", None)
        assert code == 1, out
        assert "refusing to publish" in out, out

    def test_a_suffix_that_only_looks_stable_is_still_a_pre_release(self) -> None:
        """The stable pattern is anchored at both ends: v0.7.0.1 or v0.7.0x must not pass as stable."""
        code, out, called = _run_guard("refs/tags/v0.7.0x", "ahead")
        assert (code, called) == (0, False), out


if __name__ == "__main__":
    unittest.main()
