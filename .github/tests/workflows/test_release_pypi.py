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


class ReleasePypiPublishesFromMainOnly(unittest.TestCase):
    def test_the_publish_job_is_gated_on_main(self) -> None:
        """The ref clause lives on the job, so a trigger added later cannot publish from a branch."""
        condition = re.search(r"^    if: (.*)$", code_text(WORKFLOW), re.MULTILINE)
        assert condition, "no job-level `if:`"
        assert "&& github.ref == 'refs/heads/main'" in condition[1], condition[1]


if __name__ == "__main__":
    unittest.main()
