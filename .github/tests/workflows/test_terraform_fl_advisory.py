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
"""The LZA FL ingress advisory reaches every run that can act on it (FLIP#1199).

``check-fl-plan-impact.sh`` is two things at once: a *gate* on the apply, and an *advisory* about
the cross-repo ingress contract with the networking account (``aicentre-lza-iac``). The gate belongs
to the apply alone. The advisory does not — it is ordering advice for a human, and the two runs
where a human can still act on it are the ones where the gate is absent:

* the **pull request plan**, the last point at which FLIP and ``aicentre-lza-iac`` can be sequenced
  (for a removed or replaced address the remedy is "update the networking account *first*", which is
  unactionable once the apply is running);
* an **fl_quiesced re-dispatch** of the apply, where the gate step is skipped by its own ``if:`` —
  and an NLB replacement is precisely the change that gets held, quiesced and re-dispatched.

Both must call the script in advisory-only mode, which never holds. Equally, the hold semantics must
not leak into either: a PR plan and a released re-dispatch that fail on an FL-disruptive diff would
be the gate running where it was never meant to.

Read as parsed YAML rather than text, so a reordering of the steps does not fail the test and a
renamed step does not pass it.

Usage:
    python3 .github/tests/workflows/test_terraform_fl_advisory.py
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = ROOT / ".github" / "workflows"
SCRIPT = "scripts/check-fl-plan-impact.sh"
QUIESCED_IF = "github.event_name == 'workflow_dispatch' && inputs.fl_quiesced"


def steps(workflow: str, job: str) -> list[dict[str, Any]]:
    parsed = yaml.safe_load((WORKFLOWS / workflow).read_text())
    return parsed["jobs"][job]["steps"]


# An *invocation*, not a mention: the quiesce-attestation step names the script inside a warning
# annotation, and counting that as a call would make the gate look duplicated.
INVOCATION = re.compile(rf"^\s*(?:bash\s+)?{re.escape(SCRIPT)}\b", re.MULTILINE)


def script_steps(workflow: str, job: str) -> list[dict[str, Any]]:
    """Every step whose `run:` invokes the FL plan-impact script."""
    return [s for s in steps(workflow, job) if INVOCATION.search(s.get("run") or "")]


def condition(step: dict[str, Any]) -> str:
    """The step's `if:` with the `${{ }}` wrapper stripped — it is optional in YAML, not semantic."""
    raw = str(step.get("if", "")).strip()
    if raw.startswith("${{") and raw.endswith("}}"):
        raw = raw[3:-2].strip()
    return raw


def is_advisory_only(step: dict[str, Any]) -> bool:
    """True when the step runs the script in the mode that never holds.

    Either surface counts — the `ADVISORY_ONLY` environment variable or the `--advisory-only`
    flag — because the script accepts both and the workflows should be free to pick.
    """
    env_value = str((step.get("env") or {}).get("ADVISORY_ONLY", "")).lower()
    return env_value == "true" or "--advisory-only" in (step.get("run") or "")


class PlanWorkflowRunsTheAdvisory(unittest.TestCase):
    def setUp(self) -> None:
        self.found = script_steps("terraform_plan.yml", "plan")

    def test_the_pr_plan_runs_the_script(self) -> None:
        assert self.found, (
            "terraform_plan.yml never calls " + SCRIPT + " — the LZA ingress advisory would only "
            "appear during the apply, after the merge that makes it unactionable"
        )

    def test_every_plan_invocation_is_advisory_only(self) -> None:
        for step in self.found:
            with self.subTest(step=step.get("name")):
                assert is_advisory_only(step), (
                    f"terraform_plan.yml step {step.get('name')!r} runs the gate, not the advisory: "
                    "a PR plan must not fail on an FL-disruptive diff"
                )

    def test_the_advisory_reads_a_plan_json_from_the_preserved_plan(self) -> None:
        """The script needs `terraform show -json` output, which needs the plan file to survive."""
        for step in self.found:
            with self.subTest(step=step.get("name")):
                assert "terraform show -json" in step["run"], (
                    "the advisory step must derive plan JSON from the plan file"
                )
        plan_steps = [s for s in steps("terraform_plan.yml", "plan") if "tf-via-pr" in str(s.get("uses", ""))]
        assert plan_steps, "terraform_plan.yml has no tf-via-pr plan step"
        for step in plan_steps:
            with self.subTest(step=step.get("name")):
                assert step["with"].get("preserve-plan") is True, (
                    "without preserve-plan the plan file is gone before the advisory can read it"
                )
                assert step["with"].get("upload-plan") is False, (
                    "upload-plan must stay false — the plan file contains the full tfstate"
                )


class ApplyWorkflowKeepsTheAdvisoryOnARedispatch(unittest.TestCase):
    def setUp(self) -> None:
        self.found = script_steps("terraform_apply.yml", "apply")

    def test_the_gate_is_unchanged(self) -> None:
        """Exactly one holding invocation, skipped only by the quiesce attestation."""
        gates = [s for s in self.found if not is_advisory_only(s)]
        assert len(gates) == 1, f"expected one holding gate step, found {len(gates)}"
        assert condition(gates[0]) == f"!({QUIESCED_IF})", f"the FL gate's condition changed: {gates[0]['if']!r}"

    def test_a_quiesced_redispatch_still_emits_the_advisory(self) -> None:
        advisories = [s for s in self.found if is_advisory_only(s)]
        assert advisories, (
            "terraform_apply.yml has no advisory-only invocation, so an fl_quiesced=true "
            "re-dispatch — the NLB-replacement case — applies with no LZA ingress warning at all"
        )
        conditions = [condition(s) for s in advisories]
        assert any(QUIESCED_IF in c for c in conditions), (
            f"no advisory step runs on a quiesced re-dispatch; conditions were {conditions}"
        )

    def test_the_two_invocations_are_mutually_exclusive(self) -> None:
        """One run must never both hold and advise on the same plan: the conditions are inverses."""
        gate = next(s for s in self.found if not is_advisory_only(s))
        quiesced = [s for s in self.found if is_advisory_only(s) and QUIESCED_IF in condition(s)]
        assert quiesced, "no quiesced advisory step to compare against"
        assert condition(gate) == f"!({condition(quiesced[0])})", (
            "the gate and the quiesced advisory must cover exactly complementary runs"
        )


if __name__ == "__main__":
    unittest.main()
