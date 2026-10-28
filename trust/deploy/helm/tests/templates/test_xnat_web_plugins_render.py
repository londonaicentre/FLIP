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

"""xnat-web runs the plugins built into its image: nothing is mounted over them (FLIP#1690)."""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).resolve().parents[2]
PLUGIN_DIR = "/data/xnat/home/plugins"
# What an overrides file written for the old chart may still set; Helm ignores it.
LEFTOVER = [
    "--set",
    "xnat.web.plugins.enabled=true",
    "--set",
    "xnat.web.plugins.urls.container-service=https://x/container-service-3.8.2-fat.jar",
]

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")


@pytest.mark.parametrize("extra", [[], LEFTOVER], ids=["defaults", "leftover-plugin-values"])
def test_nothing_is_mounted_over_the_image_plugins(extra: list[str]) -> None:
    """A mount at, above or inside the plugin directory would hide or change the image's jars."""
    args = ["--set", "flClient.kitHostPath=/opt/flip/fl-kit", *extra, "--show-only", "templates/xnat-web.yaml"]
    rendered = subprocess.run(
        ["helm", "template", "trust-release", str(CHART_DIR), *args], capture_output=True, text=True, timeout=120
    )
    assert rendered.returncode == 0, rendered.stderr
    (deployment,) = [d for d in yaml.safe_load_all(rendered.stdout) if d and d.get("kind") == "Deployment"]
    (tomcat,) = [c for c in deployment["spec"]["template"]["spec"]["containers"] if c["name"] == "tomcat"]

    for mount in tomcat["volumeMounts"]:
        path = mount["mountPath"].rstrip("/")
        overlaps = path == PLUGIN_DIR or PLUGIN_DIR.startswith(f"{path}/") or path.startswith(f"{PLUGIN_DIR}/")
        assert not overlaps, f"tomcat mounts {mount['name']} at {mount['mountPath']}"
