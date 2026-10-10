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

"""preflight.sh reads the kit for the environment deploy/env_mode.mk derived, not its own guess (FLIP#1390).

It used to re-map PROD itself and knew only true and stag, so PROD=lza-stag (and lza) looked for
trust/.env.<KIT>.development and refused to deploy a correctly synced LZA trust.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

CHART_DIR = Path(__file__).resolve().parents[1]
SCRIPT = CHART_DIR / "scripts" / "preflight.sh"


@pytest.mark.parametrize(("prod", "env"), [("lza-stag", "lza-stag"), ("lza", "lza-prod"), ("stag", "stag")])
def test_preflight_looks_for_the_kit_of_the_derived_environment(prod, env):
    run = subprocess.run(
        ["bash", str(SCRIPT)],
        env={**os.environ, "PROD": prod, "ENV": env, "KIT": "AZ1", "KUBE_CONTEXT": "no-such-context"},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert f"trust/.env.AZ1.{env}" in run.stdout, run.stdout[-1500:]


def test_make_preflight_hands_the_script_the_derived_environment():
    if shutil.which("make") is None:
        pytest.skip("make is not installed")
    out = subprocess.run(
        ["make", "-n", "-C", str(CHART_DIR), "preflight", "PROD=lza-stag", "KIT=AZ1"],
        capture_output=True,
        text=True,
        timeout=60,
    ).stdout
    assert 'ENV="lza-stag"' in out
