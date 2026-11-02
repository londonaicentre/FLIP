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

"""``pr_acceptance_criteria.yml`` titles a new PR after its issue only while the title is the default.

A PR whose branch is ``<n>-...`` used to get issue #n's title even when the author typed one, which
mislabels a second PR on the same issue (FLIP#1763). The step's script is run under node against a
fake GitHub client.

Usage:
    python3 .github/tests/workflows/test_pr_acceptance_criteria.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
import unittest
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[2] / "workflows" / "pr_acceptance_criteria.yml"
ISSUE_TITLE = "PR title step overwrites a typed title"


def title_script() -> str:
    """The `script: |` block of the "Set PR title from linked issue" step."""
    lines = WORKFLOW.read_text().splitlines()
    start = next(i for i, ln in enumerate(lines) if "Set PR title from linked issue" in ln)
    start = next(i for i in range(start, len(lines)) if lines[i].strip() == "script: |") + 1
    end = next(i for i in range(start, len(lines)) if lines[i].lstrip().startswith("- name:"))
    return textwrap.dedent("\n".join(lines[start:end]))


def run_step(branch: str, title: str, body: str = "## Fixes") -> list[dict]:
    """Run the step and return the ``pulls.update`` calls it made."""
    pr = {"number": 1, "head": {"ref": branch}, "title": title, "body": body}
    harness = f"""
console.log = console.error;  // the step's own logging must not mix with the JSON on stdout
const context = {{repo: {{owner: "o", repo: "r"}}, payload: {{pull_request: {json.dumps(pr)}}}}};
const calls = [];
const github = {{rest: {{
  issues: {{get: async () => ({{data: {{title: {json.dumps(ISSUE_TITLE)}}}}})}},
  pulls: {{update: async (args) => {{ calls.push(args); }}}},
}}}};
(async () => {{
{title_script()}
}})().then(() => process.stdout.write(JSON.stringify(calls)));
"""
    out = subprocess.run(["node", "-e", harness], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


@unittest.skipUnless(shutil.which("node"), "node is needed to run the github-script step")
class PrTitleFromIssue(unittest.TestCase):
    def test_default_title_is_replaced_by_the_issue_title(self) -> None:
        (call,) = run_step("1763-keep-typed-pr-title", "1763 keep typed pr title")
        assert call["title"] == ISSUE_TITLE
        assert call["body"] == "## Fixes #1763"

    def test_typed_title_is_kept(self) -> None:
        (call,) = run_step("1105-monai-overlay-suggestions", "[suggestions] MONAI overlay schema")
        assert call["title"] == "[suggestions] MONAI overlay schema"
        assert call["body"] == "## Fixes #1105"

    def test_branch_without_issue_number_is_left_alone(self) -> None:
        assert run_step("docs-tidy", "Docs tidy") == []


if __name__ == "__main__":
    unittest.main()
