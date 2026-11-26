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

"""The kit values `sync-kit` writes under trustApi.env reach trust-api's ConfigMap (FLIP#2047).

Needs helm, so the chart workflow's helm-template job runs it; the pytest job skips it.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")


def _trust_api_config(*sets: str) -> dict:
    args = ["helm", "template", "trust-release", str(CHART_DIR), "--set", "flClient.kitHostPath=/opt/flip/fl-kit"]
    for item in sets:
        args += ["--set", item]
    args += ["--show-only", "templates/trust-api.yaml"]
    rendered = subprocess.run(args, capture_output=True, text=True, check=True, timeout=120).stdout
    return next(doc for doc in yaml.safe_load_all(rendered) if doc and doc["kind"] == "ConfigMap")["data"]


def test_the_wrong_host_check_is_off_until_the_kit_names_a_trust():
    """Empty is "check off" in trust_api/config.py."""
    assert _trust_api_config()["EXPECTED_TRUST_ID"] == ""


def test_the_kits_trust_id_and_header_reach_trust_api():
    data = _trust_api_config(
        "trustApi.env.EXPECTED_TRUST_ID=11111111-1111-1111-1111-111111111111",
        "trustApi.env.TRUST_API_KEY_HEADER=X-Trust-Api-Key",
    )
    assert data["EXPECTED_TRUST_ID"] == "11111111-1111-1111-1111-111111111111"
    assert data["TRUST_API_KEY_HEADER"] == "X-Trust-Api-Key"  # pragma: allowlist secret
