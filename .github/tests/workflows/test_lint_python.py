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
"""``lint_python.yml`` runs ``ruff check`` and ``ruff format --check`` over every tracked Python file.

The service workflows lint only their own trees and only a few check formatting, so this job is the
only lint that reaches the Python outside them and the only format check covering the whole repo
(FLIP#1326). Its coverage holds only while its shape does: a path filter, a pathspec exclude, a
working directory or ruff's own gitignore-respecting discovery would each let tracked files fall
through again, silently. Read as text, like the other workflow tests.

Usage:
    python3 .github/tests/workflows/test_lint_python.py
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[2] / "workflows" / "lint_python.yml"

LINT_COMMAND = "git ls-files -z -- '*.py' '*.pyi' | xargs -0 ruff check --force-exclude --no-fix --output-format=github"
FORMAT_COMMAND = "git ls-files -z -- '*.py' '*.pyi' | xargs -0 ruff format --check --force-exclude"
# The one step condition allowed: the format check still runs when the lint step failed.
NOT_CANCELLED = "${{ !cancelled() }}"


def code_text() -> str:
    """The workflow without its comment lines, so prose never satisfies a check."""
    lines = WORKFLOW.read_text().splitlines()
    return "\n".join(line for line in lines if not line.lstrip().startswith("#")) + "\n"


class LintPythonCoversEveryTrackedFile(unittest.TestCase):
    def test_it_runs_on_every_push_and_pull_request_to_the_long_lived_branches(self) -> None:
        text = code_text()
        triggers = re.search(r"^on:\n(?P<body>(?:  [^\n]*\n)+)", text, re.MULTILINE)
        assert triggers, "lint_python.yml has no `on:` block"
        assert triggers["body"] == (
            "  push:\n    branches: [main, develop]\n  pull_request:\n    branches: [main, develop]\n"
        ), f"a filtered or narrowed trigger lets files fall through:\n{triggers['body']}"

    def test_it_checks_the_tracked_file_list_not_ruffs_own_discovery(self) -> None:
        """Discovery skips gitignored paths, so a tracked file under one is never checked; the list
        comes from git instead. --force-exclude keeps each config's deliberate excludes."""
        runs = re.findall(r"^\s+run: (.*)$", code_text(), re.MULTILINE)
        assert LINT_COMMAND in runs, runs
        assert FORMAT_COMMAND in runs, runs

    def test_it_runs_from_the_repo_root_and_cannot_be_skipped(self) -> None:
        text = code_text()
        for construct in ("working-directory:", "continue-on-error:", "\n    if:"):
            assert construct not in text, construct
        step_conditions = re.findall(r"^\s{8}if: (.*)$", text, re.MULTILINE)
        assert step_conditions == [NOT_CANCELLED], f"only the format step may carry a condition: {step_conditions}"

    def test_ruff_is_pinned_exactly(self) -> None:
        """Preview rules change between releases; a floating version turns a ruff upgrade into red CI."""
        assert re.search(r"^\s+run: pip install ruff==\d+\.\d+\.\d+$", code_text(), re.MULTILINE)


if __name__ == "__main__":
    unittest.main()
