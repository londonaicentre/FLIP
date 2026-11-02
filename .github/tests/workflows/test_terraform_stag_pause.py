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
on ``main`` has to let ``main`` through. The fork skips share those ``if:`` expressions, so they are
pinned here too. Read as text, like the other workflow tests.

Usage:
    python3 .github/tests/workflows/test_terraform_stag_pause.py
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[2] / "workflows"
GUARD = "vars.TF_STAG_DISABLED != 'true'"
FORK_SKIP = "(github.repository == 'londonaicentre/FLIP' || vars.TF_CI_ON_FORK == 'true')"


def job_if(workflow: str, job: str) -> str:
    """Return the job-level ``if:`` expression of ``job`` in ``workflow`` ('' when there is none)."""
    text = (WORKFLOWS / workflow).read_text()
    block = re.search(rf"^  {job}:\n(?P<body>(?:    [^\n]*\n|\s*\n)*)", text, re.MULTILINE)
    assert block, f"{workflow}: no job '{job}'"
    folded = re.search(r"^    if: >-\n(?P<expr>(?:      [^\n]*\n)+)", block["body"], re.MULTILINE)
    if folded:
        return " ".join(line.strip() for line in folded["expr"].splitlines())
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
        """apply and drift serve main too; the guard must be OR-ed with the main check, not AND-ed.

        The jobs may carry further conditions (they wait on the staging start and state jobs), so
        the guard is checked as the parenthesised OR clause, and it must appear nowhere else.
        """
        clause = f"(github.ref_name == 'main' || {GUARD})"
        for workflow, job in (("terraform_apply.yml", "apply"), ("terraform_drift.yml", "drift")):
            with self.subTest(workflow=workflow):
                expr = job_if(workflow, job)
                assert expr == f"github.ref_name == 'main' || {GUARD}" or clause in expr, expr
                assert expr.count(GUARD) == 1, f"{workflow}:{job} reads the guard outside the main clause"

    def test_the_plan_keeps_its_fork_pr_skip(self) -> None:
        assert job_if("terraform_plan.yml", "plan") == (
            f"{FORK_SKIP} && github.event.pull_request.head.repo.full_name == github.repository && {GUARD}"
        )

    def test_every_terraform_job_skips_forks_that_have_not_opted_in(self) -> None:
        """A fork has no aws-* environments, so each run fails and mails whoever pushed or last edited the file."""
        for workflow, job in (
            ("terraform_plan.yml", "plan"),
            ("terraform_apply.yml", "apply"),
            ("terraform_drift.yml", "drift"),
            ("terraform_drift.yml", "dispatch-prod"),
            ("terraform_apply.yml", "start-staging"),
            ("terraform_drift.yml", "staging-state"),
            ("staging_lifecycle.yml", "nightly-guard"),
            ("staging_lifecycle.yml", "hold"),
            ("staging_lifecycle.yml", "power"),
        ):
            with self.subTest(workflow=workflow, job=job):
                assert job_if(workflow, job).startswith(f"{FORK_SKIP} && ")

    def test_the_production_drift_dispatch_does_not_wait_on_the_paused_job(self) -> None:
        text = (WORKFLOWS / "terraform_drift.yml").read_text()
        block = re.search(r"^  dispatch-prod:\n(?P<body>(?:    [^\n]*\n|\s*\n)*)", text, re.MULTILINE)
        assert block, "terraform_drift.yml has no dispatch-prod job"
        assert "needs:" not in block["body"], "dispatch-prod must not depend on the drift job"


if __name__ == "__main__":
    unittest.main()
