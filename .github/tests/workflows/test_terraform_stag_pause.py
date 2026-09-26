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
"""``TF_STAG_DISABLED`` pauses the staging leg of the Terraform workflows, and only that leg.

With the repository variable set to ``true``, no staging plan, apply or drift job may start: the
aws-stag environment would otherwise rebuild a staging estate that was torn down on purpose on the
next develop merge. Production must never read the variable, so every guard on a job that can run
on ``main`` has to let ``main`` through. Read as text, like the other workflow tests.

Usage:
    python3 .github/tests/workflows/test_terraform_stag_pause.py
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[2] / "workflows"
GUARD = "vars.TF_STAG_DISABLED != 'true'"


def job_if(workflow: str, job: str) -> str:
    """Return the job-level ``if:`` expression of ``job`` in ``workflow`` ('' when there is none)."""
    text = (WORKFLOWS / workflow).read_text()
    block = re.search(rf"^  {job}:\n(?P<body>(?:    [^\n]*\n|\s*\n)*)", text, re.MULTILINE)
    assert block, f"{workflow}: no job '{job}'"
    found = re.search(r"^    if: (?P<expr>[^\n]+)$", block["body"], re.MULTILINE)
    return found["expr"] if found else ""


class TerraformStagPause(unittest.TestCase):
    def test_every_staging_job_is_paused_by_the_variable(self) -> None:
        for workflow, job in (
            ("terraform_plan.yml", "plan"),
            ("terraform_apply.yml", "apply"),
            ("terraform_drift.yml", "drift"),
        ):
            with self.subTest(workflow=workflow):
                assert GUARD in job_if(workflow, job), f"{workflow}:{job} ignores TF_STAG_DISABLED"

    def test_production_legs_never_read_the_variable(self) -> None:
        """apply and drift serve main too; the guard must be OR-ed with the main check, not AND-ed."""
        for workflow, job in (("terraform_apply.yml", "apply"), ("terraform_drift.yml", "drift")):
            with self.subTest(workflow=workflow):
                assert job_if(workflow, job) == f"github.ref_name == 'main' || {GUARD}"

    def test_the_plan_keeps_its_fork_skip(self) -> None:
        assert job_if("terraform_plan.yml", "plan") == (
            f"github.event.pull_request.head.repo.full_name == github.repository && {GUARD}"
        )

    def test_the_production_drift_dispatch_does_not_wait_on_the_paused_job(self) -> None:
        text = (WORKFLOWS / "terraform_drift.yml").read_text()
        block = re.search(r"^  dispatch-prod:\n(?P<body>(?:    [^\n]*\n|\s*\n)*)", text, re.MULTILINE)
        assert block and "needs:" not in block["body"], "dispatch-prod must not depend on the drift job"


if __name__ == "__main__":
    unittest.main()
