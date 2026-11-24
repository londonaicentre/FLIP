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
"""Every workflow installs graphviz through the ``install-graphviz`` action, which retries a mirror outage (FLIP#1988).

A bare ``apt-get update && apt-get install graphviz`` failed whole jobs on one unreachable Ubuntu mirror. The
retry holds only while no workflow installs graphviz its own way again, so this scans every workflow, and runs the
action's script against stub ``apt-get``/``sleep`` to prove the retry itself.

Usage:
    python3 .github/tests/actions/test_install_graphviz.py
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

GITHUB_DIR = Path(__file__).resolve().parents[2]
ACTION = GITHUB_DIR / "actions" / "install-graphviz" / "action.yml"
USE = "uses: ./.github/actions/install-graphviz"
WORKFLOWS = sorted([*(GITHUB_DIR / "workflows").glob("*.yml"), *(GITHUB_DIR / "workflows").glob("*.yaml")])
BARE_INSTALL = re.compile(r"\bapt(?:-get)?\b.*\bgraphviz\b")


def code_lines(path: Path) -> list[str]:
    """The file's lines without comments, so prose never satisfies or trips a check."""
    return [line for line in path.read_text().splitlines() if not line.lstrip().startswith("#")]


def action_script() -> str:
    """The action's ``run: |`` block, dedented."""
    lines = ACTION.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "run: |") + 1
    body = [line for line in lines[start:] if line.startswith("        ") or not line.strip()]
    return "\n".join(line[8:] for line in body) + "\n"


class EveryWorkflowUsesTheAction(unittest.TestCase):
    def test_no_workflow_installs_graphviz_with_apt_itself(self) -> None:
        bare = [f"{w.name}: {line.strip()}" for w in WORKFLOWS for line in code_lines(w) if BARE_INSTALL.search(line)]
        assert not bare, f"install graphviz with `{USE}`, which retries a mirror outage:\n" + "\n".join(bare)

    def test_the_jobs_that_render_diagrams_use_it(self) -> None:
        uses = {w.name: sum(USE in line for line in code_lines(w)) for w in WORKFLOWS}
        assert uses["docs.yml"] == 1, uses
        # AWS deploy tests, Azure node checks and the AWS EC2/EKS node matrix.
        for name in ("validate_terraform.yml", "validate_terraform_azure.yml", "validate_terraform_aws_node.yml"):
            assert uses[name] == 1, uses

    def test_each_job_checks_out_the_repo_before_using_it(self) -> None:
        """A local action is read from the checkout: used before actions/checkout, the step cannot even load."""
        for workflow in WORKFLOWS:
            lines = code_lines(workflow)
            for i, line in enumerate(lines):
                if USE not in line:
                    continue
                steps = max(j for j in range(i) if lines[j].strip() == "steps:")
                assert any("uses: actions/checkout@" in s for s in lines[steps:i]), f"{workflow.name}:{i}"


class TheActionRetries(unittest.TestCase):
    def _run(self, failures: int) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        """Run the action's script with apt-get failing its first ``failures`` calls; sudo and sleep are stubs."""
        with tempfile.TemporaryDirectory() as td:
            bin_dir, calls = Path(td, "bin"), Path(td, "calls")
            bin_dir.mkdir()
            stubs = {
                "sudo": 'exec "$@"',
                "sleep": 'echo "sleep $1" >> "$CALLS"',
                "dot": "echo 'dot - graphviz version stub'",
                "apt-get": (
                    'echo "apt-get $*" >> "$CALLS"\nn=$(grep -c "^apt-get" "$CALLS")\n[ "$n" -gt "$FAILURES" ]'
                ),
            }
            for name, body in stubs.items():
                (bin_dir / name).write_text(f"#!/bin/bash\n{body}\n")
                (bin_dir / name).chmod(0o755)
            env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "CALLS": str(calls)}
            env["FAILURES"] = str(failures)
            result = subprocess.run(["bash", "-c", action_script()], env=env, capture_output=True, text=True)
            return result, calls.read_text().splitlines() if calls.exists() else []

    def test_a_mirror_back_after_two_failures_installs(self) -> None:
        result, calls = self._run(failures=2)
        assert result.returncode == 0, result.stdout + result.stderr
        assert [c for c in calls if c.startswith("sleep")] == ["sleep 30", "sleep 60"], calls
        assert result.stdout.count("::warning::") == 2, result.stdout
        assert "dot - graphviz version stub" in result.stdout, "the install is checked with dot -V"

    def test_a_mirror_down_throughout_fails_after_four_attempts(self) -> None:
        result, calls = self._run(failures=99)
        assert result.returncode == 1, result.stdout
        assert sum(c.startswith("apt-get") for c in calls) == 4, "four updates, each failing before its install"
        assert [c for c in calls if c.startswith("sleep")] == ["sleep 30", "sleep 60", "sleep 90"], calls
        assert "::error::graphviz could not be installed after 4 attempts" in result.stdout, result.stdout

    def test_every_apt_call_carries_the_retries_and_the_timeouts(self) -> None:
        _, calls = self._run(failures=0)
        apt = [c for c in calls if c.startswith("apt-get")]
        assert len(apt) == 2, apt
        for call in apt:
            for opt in ("Acquire::Retries=3", "Acquire::http::Timeout=30", "Acquire::https::Timeout=30"):
                assert opt in call, call


if __name__ == "__main__":
    unittest.main()
