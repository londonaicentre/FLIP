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

"""`make status` expects an fl-client pod only when the release enables it (FLIP#1487)."""

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "check_status.py"

# Stands in for kubectl and helm: every component has one ready pod except fl-client, which has
# none; `helm get values` answers $VALUES.
STUB = """#!/bin/sh
case "$*" in
  *"get nodes -o name"*) echo node/n1 ;;
  *"get ns "*) echo "flip-trust Active" ;;
  *"get values"*) printf '%s' "$VALUES" ;;
  *"component=fl-client"*) ;;
  *"-o name"*) echo pod/p1 ;;
  *"jsonpath={.status.containerStatuses[0].ready}"*) echo true ;;
  *"list -n"*) echo "[]" ;;
esac
"""


def _fl_client_lines(tmp_path: Path, values: str) -> list[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("kubectl", "helm"):
        (bin_dir / name).write_text(STUB)
        (bin_dir / name).chmod(0o755)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--skip-endpoints", "--skip-gpu"],
        capture_output=True,
        text=True,
        env={"PATH": f"{bin_dir}:/usr/bin:/bin", "VALUES": values},
        timeout=120,
    )
    assert "Summary" in result.stdout, result.stdout + result.stderr
    return [line for line in result.stdout.splitlines() if "fl-client" in line or "FL client" in line]


def test_a_release_with_the_fl_client_disabled_says_so_and_does_not_fail(tmp_path):
    lines = _fl_client_lines(tmp_path, '{"flClient": {"enabled": false}}')

    assert any("FL client disabled" in line for line in lines), lines
    assert not [line for line in lines if "FAIL" in line], lines


@pytest.mark.parametrize("values", ['{"flClient": {"enabled": true}}', ""], ids=["enabled", "values-unreadable"])
def test_a_missing_fl_client_still_fails_unless_the_release_disables_it(tmp_path, values):
    lines = _fl_client_lines(tmp_path, values)

    assert any("FAIL" in line and "No pods for 'fl-client'" in line for line in lines), lines
