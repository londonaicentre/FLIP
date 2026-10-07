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

Read as text, like the other workflow tests. The runner these execute on has no YAML parser
installed, and depending on one would make this the only test in the directory that cannot run
there.

Usage:
    python3 .github/tests/workflows/test_terraform_fl_advisory.py
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[2] / "workflows"
SCRIPT = "scripts/check-fl-plan-impact.sh"
QUIESCED_IF = "github.event_name == 'workflow_dispatch' && inputs.fl_quiesced"

# An *invocation*, not a mention: the quiesce-attestation step names the script inside a warning
# annotation, and counting that as a call would make the gate look duplicated.
INVOCATION = re.compile(rf"^\s*(?:bash\s+)?{re.escape(SCRIPT)}\b", re.MULTILINE)


def steps(workflow: str) -> list[str]:
    """The workflow's step blocks, each the text from its ``- name:`` to the next step or EOF.

    Only one job per workflow here declares steps, so the blocks need no job filter.
    """
    text = (WORKFLOWS / workflow).read_text()
    starts = [m.start() for m in re.finditer(r"^      - name: ", text, re.MULTILINE)]
    assert starts, f"{workflow}: no steps found — has the indentation changed?"
    bounds = starts + [len(text)]
    return [text[a:b] for a, b in zip(bounds, bounds[1:])]


def name_of(step: str) -> str:
    return step.split("\n", 1)[0].removeprefix("      - name: ").strip()


def condition(step: str) -> str:
    """The step's ``if:``, with the optional ``${{ }}`` wrapper stripped — it is not semantic."""
    found = re.search(r"^        if: (?P<expr>.+)$", step, re.MULTILINE)
    if not found:
        return ""
    raw = found["expr"].strip()
    if raw.startswith("${{") and raw.endswith("}}"):
        raw = raw[3:-2].strip()
    return raw


def script_steps(workflow: str) -> list[str]:
    """Every step that invokes the FL plan-impact script."""
    return [s for s in steps(workflow) if INVOCATION.search(s)]


def is_advisory_only(step: str) -> bool:
    """True when the step runs the script in the mode that never holds.

    Either surface counts — the ``ADVISORY_ONLY`` environment variable or the ``--advisory-only``
    flag — because the script accepts both and the workflows should be free to pick.
    """
    env_set = re.search(r'^\s+ADVISORY_ONLY: "?true"?\s*$', step, re.MULTILINE)
    return bool(env_set) or "--advisory-only" in step


def with_input(step: str, key: str) -> str | None:
    """The value of a ``with:`` input on an `uses:` step, or None when it is absent."""
    found = re.search(rf"^          {re.escape(key)}: (?P<value>.+)$", step, re.MULTILINE)
    return found["value"].strip() if found else None


class PlanWorkflowRunsTheAdvisory(unittest.TestCase):
    def setUp(self) -> None:
        self.found = script_steps("terraform_plan.yml")

    def test_the_pr_plan_runs_the_script(self) -> None:
        assert self.found, (
            f"terraform_plan.yml never calls {SCRIPT} — the LZA ingress advisory would only "
            "appear during the apply, after the merge that makes it unactionable"
        )

    def test_every_plan_invocation_is_advisory_only(self) -> None:
        for step in self.found:
            with self.subTest(step=name_of(step)):
                assert is_advisory_only(step), (
                    f"terraform_plan.yml step {name_of(step)!r} runs the gate, not the advisory: "
                    "a PR plan must not fail on an FL-disruptive diff"
                )

    def test_the_advisory_derives_plan_json_from_the_plan_file(self) -> None:
        for step in self.found:
            with self.subTest(step=name_of(step)):
                msg = "the advisory step must derive plan JSON from the plan file"
                assert "terraform show -json" in step, msg

    def test_the_plan_file_survives_for_it_and_is_never_published(self) -> None:
        # `uses:`, not a bare substring — a neighbouring step's comment mentions the action.
        plan_steps = [
            s for s in steps("terraform_plan.yml") if re.search(r"^        uses: op5dev/tf-via-pr", s, re.MULTILINE)
        ]
        assert plan_steps, "terraform_plan.yml has no tf-via-pr plan step"
        for step in plan_steps:
            with self.subTest(step=name_of(step)):
                assert with_input(step, "preserve-plan") == "true", (
                    "without preserve-plan the plan file is gone before the advisory can read it"
                )
                assert with_input(step, "upload-plan") == "false", (
                    "upload-plan must stay false — the plan file contains the full tfstate"
                )


class ApplyWorkflowKeepsTheAdvisoryOnARedispatch(unittest.TestCase):
    def setUp(self) -> None:
        self.found = script_steps("terraform_apply.yml")

    def test_the_gate_is_unchanged(self) -> None:
        """Exactly one holding invocation, skipped only by the quiesce attestation."""
        gates = [s for s in self.found if not is_advisory_only(s)]
        assert len(gates) == 1, f"expected one holding gate step, found {[name_of(s) for s in gates]}"
        changed = f"the FL gate's condition changed: {condition(gates[0])!r}"
        assert condition(gates[0]) == f"!({QUIESCED_IF})", changed

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
