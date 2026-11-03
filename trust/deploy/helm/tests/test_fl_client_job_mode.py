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

"""trust-api reports "pods" exactly when NVFLARE job pods are on, so the hub knows how jobs run (FLIP#1739)."""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

CHART_DIR = Path(__file__).resolve().parents[1]


def _render(*extra: str) -> str:
    return subprocess.run(
        ["helm", "template", "t", str(CHART_DIR), "--set", "flClient.enabled=true", "--set", "flClient.kitHostPath=/k"]
        + list(extra),
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "HELM_NAMESPACE": "flip-trust"},
    ).stdout


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")
@pytest.mark.parametrize(
    ("values", "mode"),
    [
        ((), "process"),
        (("--set", "flClient.nvflare.jobPods.enabled=true", "--set", "flClient.gpu.enabled=false"), "pods"),
        (("--set", "flBackend=flower"), "process"),
        # Only reachable with the fl-client off (the chart refuses job pods on Flower otherwise): pins the
        # backend check.
        (
            (
                "--set",
                "flBackend=flower",
                "--set",
                "flClient.enabled=false",
                "--set",
                "flClient.nvflare.jobPods.enabled=true",
            ),
            "process",
        ),
    ],
)
def test_trust_api_reports_how_the_trust_runs_fl_jobs(values, mode):
    out = _render(*values)
    assert re.findall(r'FL_CLIENT_JOB_MODE: "(\w+)"', out) == [mode]
    # trust-api reads the mode once, at start, so its pod template carries it too: a changed mode rolls the pod.
    assert re.findall(r'flip-trust/fl-client-job-mode: "(\w+)"', out) == [mode]
