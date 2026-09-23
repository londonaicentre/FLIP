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

import re
import sys
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


if __name__ == "__main__":
    unittest.main()
