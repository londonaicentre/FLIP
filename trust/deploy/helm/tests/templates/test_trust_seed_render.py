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

"""The trust-seed Job, rendered: what trust/seed_trust.sh is told (FLIP#2111).

The hook seeds only an empty store, so `make reseed` (trustData.seed.reseed=true) is the one way a
seeded trust reaches a new trustData.version; the value has to arrive as RESEED in the container,
and the hook must not retry. Skipped without helm.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")


def _job(*sets: str) -> dict:
    args = ["helm", "template", "trust-release", str(CHART_DIR), "--set", "flClient.kitHostPath=/opt/flip/fl-kit"]
    for item in sets:
        args += ["--set", item]
    result = subprocess.run(args, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    for doc in yaml.safe_load_all(result.stdout):
        if doc and doc.get("kind") == "Job" and doc["metadata"]["name"].endswith("-trust-seed"):
            return doc
    raise AssertionError("no trust-seed Job rendered")


def _env(job: dict) -> dict[str, str]:
    (container,) = job["spec"]["template"]["spec"]["containers"]
    return {e["name"]: e.get("value") for e in container["env"]}


@pytest.mark.parametrize(("sets", "reseed"), [((), "false"), (("trustData.seed.reseed=true",), "true")])
def test_reseed_reaches_the_container(sets, reseed):
    assert _env(_job(*sets))["RESEED"] == reseed


def test_the_hook_does_not_retry():
    """A retry would find the store a failed attempt part-loaded, skip it, and report success."""
    assert _job()["spec"]["backoffLimit"] == 0
