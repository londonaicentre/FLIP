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
"""``release-pypi.yml`` publishes flip-utils from ``main`` and nowhere else (FLIP#1743).

The job holds `contents: write` and PyPI trusted publishing, and a PyPI release cannot be undone —
a version number is never re-usable. The ref gate therefore lives on the job, not only in the
trigger's branch filter, so that adding a trigger (a dispatch to re-run a failed publish, a
workflow_run) cannot reach `uv publish`, the tag push or the GitHub release from a branch.

Usage:
    python3 .github/tests/workflows/test_release_pypi.py
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from image_workflows import WORKFLOWS, code_text  # noqa: E402

WORKFLOW = WORKFLOWS / "release-pypi.yml"
# Steps that leave the runner and cannot be taken back.
PUBLISHING_STEPS = ("Publish to PyPI", "Create and push git tag", "Create GitHub Release")


class ReleasePypiPublishesFromMainOnly(unittest.TestCase):
    def setUp(self) -> None:
        self.text = code_text(WORKFLOW)

    def test_every_job_carries_the_main_ref_gate(self) -> None:
        """Read off the job's `if:` rather than the trigger: the trigger is what a later change adds to."""
        jobs = re.findall(r"^  (\w[\w-]*):\n((?:(?:    .*)?\n)+)", self.text.split("\njobs:\n", 1)[1], re.MULTILINE)
        assert jobs, "no jobs parsed"
        for name, body in jobs:
            with self.subTest(job=name):
                condition = re.search(r"^    if: (.*)$", body, re.MULTILINE)
                assert condition, f"{name}: no job-level `if:`"
                assert "github.ref == 'refs/heads/main'" in condition[1], f"{name}: {condition[1]}"

    def test_the_gate_is_an_and_not_an_or(self) -> None:
        """`||` between the repository clause and the ref clause would let either one carry the job."""
        condition = re.search(r"^    if: (.*)$", self.text, re.MULTILINE)
        assert condition, "no job-level `if:`"
        assert "||" not in condition[1], condition[1]
        assert "github.repository == 'londonaicentre/FLIP'" in condition[1], condition[1]

    def test_the_irreversible_steps_are_in_the_gated_job(self) -> None:
        """A publishing step moved into a second, ungated job would pass the test above vacuously."""
        gated = self.text.split("\njobs:\n", 1)[1]
        gated = gated[gated.index("if: github.repository") :]
        for step in PUBLISHING_STEPS:
            with self.subTest(step=step):
                assert f"- name: {step}" in gated, step

    def test_the_trigger_is_still_a_push_to_main(self) -> None:
        """The gate is a second line of defence, not a replacement for the branch filter."""
        push = re.search(r"^on:\n  push:\n    branches:\n      - main$", self.text, re.MULTILINE)
        assert push, "release-pypi.yml no longer triggers on a push to main"

    def test_the_publish_still_skips_an_already_released_version(self) -> None:
        """Unchanged behaviour: the tag-existence check is what makes a re-run of main safe."""
        assert "- name: Check if tag already exists" in self.text
        for step in PUBLISHING_STEPS:
            with self.subTest(step=step):
                body = self.text[self.text.index(f"- name: {step}") :].split("- name:", 2)[1]
                assert "if: steps.check_tag.outputs.skip == 'false'" in body, step


if __name__ == "__main__":
    unittest.main()
