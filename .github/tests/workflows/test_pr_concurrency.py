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
"""Every PR-triggered workflow cancels its superseded PR runs, and only those (FLIP#2021).

Without the block each push to a PR leaves the previous commit's runs going to completion. The
group must fall back to the run id off a PR: keyed on the ref instead, runs on develop or main
would share a group, where GitHub keeps a single pending run and cancels the one before it, which
drops a test run an image build waits on. Read as text, like the other workflow tests.

Usage:
    python3 .github/tests/workflows/test_pr_concurrency.py
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[2] / "workflows"

BLOCK = (
    "concurrency:\n"
    "  group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.run_id }}\n"
    "  cancel-in-progress: true\n"
)
EXEMPT = {
    # Their own group, with a prefix: both already cancel a PR's superseded run.
    "lighthouse_flip_ui.yml",
    "terraform_plan.yml",
    # Seconds long, and they act on the PR itself (its body, its target, a comment).
    "pr_acceptance_criteria.yml",
    "validate_branch_origin.yml",
    "pr-release-notes-preview.yml",
}


def pr_workflows() -> list[Path]:
    return [w for w in sorted(WORKFLOWS.glob("*.yml")) if re.search(r"^  pull_request:", w.read_text(), re.MULTILINE)]


class PrRunsCancelWhenSuperseded(unittest.TestCase):
    def test_every_pr_workflow_carries_the_block(self) -> None:
        missing = [w.name for w in pr_workflows() if w.name not in EXEMPT and BLOCK not in w.read_text()]
        assert not missing, f"add the concurrency block (see AGENTS.md, PR concurrency): {missing}"

    def test_the_exemptions_are_still_pr_workflows(self) -> None:
        assert EXEMPT <= {w.name for w in pr_workflows()}, EXEMPT

    def test_workflow_names_are_unique(self) -> None:
        """The group starts with the workflow name: two workflows sharing one would cancel each other."""
        names = [re.search(r"^name: (.+)$", w.read_text(), re.MULTILINE)[1] for w in pr_workflows()]
        assert len(names) == len(set(names)), names


if __name__ == "__main__":
    unittest.main()
