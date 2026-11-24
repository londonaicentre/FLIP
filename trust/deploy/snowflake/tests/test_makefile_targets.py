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

"""The node Makefile carries the epic's whole target list (#1589 acceptance criterion 1)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

SNOWFLAKE_DIR = Path(__file__).resolve().parents[1]
TARGETS = ("plan", "apply", "destroy", "sync-kit", "up", "down", "suspend", "status", "selftest")


def dry(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["make", "-n", "-C", str(SNOWFLAKE_DIR), *args], capture_output=True, text=True)


@pytest.mark.parametrize("target", TARGETS)
def test_every_lifecycle_target_exists(target: str) -> None:
    result = dry(target, "KIT=GSTT")
    assert "No rule to make target" not in result.stderr, result.stderr


@pytest.mark.parametrize(
    ("root", "directory"),
    [
        ("workload", "trust/deploy/terraform/snowflake"),
        ("platform", "trust/deploy/terraform/snowflake-platform"),
        ("identity", "trust/deploy/terraform/snowflake-identity"),
    ],
)
@pytest.mark.parametrize("target", ["plan", "apply", "destroy"])
def test_infrastructure_targets_forward_to_the_terraform_root(target: str, root: str, directory: str) -> None:
    if not (SNOWFLAKE_DIR.parents[2] / directory / "Makefile").is_file():
        pytest.skip(f"{directory} is not on this branch yet (it lands with #1599)")
    result = dry(target, f"TF_ROOT={root}")
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert f"{directory} {target}" in out, out


def test_an_unknown_root_is_refused() -> None:
    result = subprocess.run(
        ["make", "-C", str(SNOWFLAKE_DIR), "plan", "TF_ROOT=bootstrap"], capture_output=True, text=True
    )
    assert result.returncode != 0
    assert "TF_ROOT must be workload, platform or identity" in result.stdout


@pytest.mark.parametrize("target", ["render", "up", "status"])
def test_hub_egress_on_the_make_line_is_refused(target: str) -> None:
    """The kit holds it: a value given for one run is gone on the next, which detaches the trust."""
    result = dry(target, "KIT=GSTT", "SNOW_CONNECTION=x", "HUB_EGRESS=true")
    assert result.returncode != 0
    assert "HUB_EGRESS" in result.stderr
    assert "kit" in result.stderr
