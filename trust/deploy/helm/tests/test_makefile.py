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

"""The chart Makefile's release upgrade reads the kit the site was synced from, for every PROD (FLIP#1204).

``upgrade-trust-k8s`` hands scripts/site_upgrade.py ``trust/.env.<KIT>.<env>``; the env token comes
from ``deploy/env_mode.mk`` like every other Makefile that reads PROD, so an LZA site's kit is
``.lza-prod`` / ``.lza-stag`` rather than a development kit that does not exist.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

CHART_DIR = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")


def _kit_file(prod: str) -> str:
    out = subprocess.run(
        ["make", "-n", "-C", str(CHART_DIR), "upgrade-trust-k8s", "KIT=ABC", f"PROD={prod}", "TAG=v0.7.0"],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    ).stdout
    line = next(line for line in out.splitlines() if "site_upgrade.py plan" in line)
    return line.split("--kit-file ", 1)[1].split()[0]


@pytest.mark.parametrize(
    ("prod", "suffix"),
    [("", "development"), ("stag", "stag"), ("true", "production"), ("lza", "lza-prod"), ("lza-stag", "lza-stag")],
)
def test_upgrade_reads_the_kit_for_its_environment(prod, suffix):
    assert _kit_file(prod).endswith(f"/trust/.env.ABC.{suffix}")
