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

"""xnat-web's probes on the rendered Deployment; templates/xnat-web.yaml says why (FLIP#1456, item H5)."""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).resolve().parents[2]
TIMING = {"initialDelaySeconds", "periodSeconds", "timeoutSeconds", "failureThreshold", "successThreshold"}

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")


@pytest.fixture(scope="module")
def tomcat() -> dict:
    """The rendered xnat-web ``tomcat`` container, at the chart defaults."""
    args = ["--set", "flClient.kitHostPath=/opt/flip/fl-kit", "--show-only", "templates/xnat-web.yaml"]
    rendered = subprocess.run(
        ["helm", "template", "trust-release", str(CHART_DIR), *args], capture_output=True, text=True, timeout=120
    )
    assert rendered.returncode == 0, rendered.stderr
    (deployment,) = [d for d in yaml.safe_load_all(rendered.stdout) if d and d.get("kind") == "Deployment"]

    return next(c for c in deployment["spec"]["template"]["spec"]["containers"] if c["name"] == "tomcat")


@pytest.mark.parametrize(("probe", "path"), [("readinessProbe", "/xapi/siteConfig/buildInfo"), ("livenessProbe", "/")])
def test_probe_is_an_anonymous_get_of_its_path(tomcat: dict, probe: str, path: str) -> None:
    """Compared exactly, so a swapped path, headers (credentials), a scheme or an exec handler all fail."""
    assert probe in tomcat, f"xnat-web renders no {probe}"
    handler = {key: value for key, value in tomcat[probe].items() if key not in TIMING}

    assert handler == {"httpGet": {"path": path, "port": "tomcat"}}, f"xnat-web {probe} is {handler}"
