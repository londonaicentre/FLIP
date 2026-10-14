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

"""The xnat-init Job refuses an xnat-web image whose configure-dcm2niix.sh predates FLIP#1451.

The chart's configure-dcm2niix container runs the script the xnat-web image ships, and the image
follows ``xnat.image.pin`` (the kit's XNAT_TAG). A pin on an image built before FLIP#1451, v0.12.0
say, hands the chart a Docker-only script that dies under ``set -u`` on DATA_PATH, which a
``helm upgrade`` reports only as a failed hook. These tests render the Job with such a pin, run its
container command for real against a stand-in script and check it stops first, naming the image.

Skipped without helm; the chart workflow's helm-template job runs it.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).resolve().parents[2]
REPOSITORY = "ghcr.io/londonaicentre/xnat-web"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")


def _container_command(*sets: str) -> str:
    """The configure-dcm2niix container's shell, as the chart renders it with ``--set`` ``sets``."""
    args = ["helm", "template", "trust-release", str(CHART_DIR), "--set", "flClient.kitHostPath=/opt/flip/fl-kit"]
    for item in sets:
        args += ["--set", item]
    args += ["--show-only", "templates/xnat-init-job.yaml"]
    rendered = subprocess.run(args, capture_output=True, text=True, check=True, timeout=120).stdout
    (job,) = [doc for doc in yaml.safe_load_all(rendered) if doc and doc["kind"] == "Job"]
    (container,) = [c for c in job["spec"]["template"]["spec"]["containers"] if c["name"] == "configure-dcm2niix"]
    assert container["command"][:2] == ["/bin/bash", "-c"]
    return str(container["command"][2])


def _run(tmp_path: Path, command: str, script: str) -> subprocess.CompletedProcess[str]:
    """Runs ``command`` with the image's config directory stood in for by ``tmp_path``."""
    (tmp_path / "configure-dcm2niix.sh").write_text(script)
    command = command.replace("cd /data/xnat/config", f"cd {tmp_path}")
    return subprocess.run(["bash", "-c", command], capture_output=True, text=True, timeout=60)


def test_an_image_from_before_the_backend_switch_is_refused_by_name(tmp_path: Path) -> None:
    command = _container_command("xnat.image.pin=v0.12.0")
    # v0.12.0's script, reduced to the line that kills it under the chart.
    result = _run(tmp_path, command, 'set -u\necho "DATA_PATH=$DATA_PATH"\necho ran\n')

    assert result.returncode != 0
    assert f"{REPOSITORY}:v0.12.0 predates FLIP#1451" in result.stderr
    assert "xnat.image.pin" in result.stderr
    assert "ran" not in result.stdout, "the old script ran anyway"


def test_an_image_with_the_backend_switch_runs_its_script(tmp_path: Path) -> None:
    command = _container_command()
    result = _run(tmp_path, command, 'echo "backend=${XNAT_CS_BACKEND:-unset}"\n')

    assert result.returncode == 0, result.stderr
    assert "backend=unset" in result.stdout, "the script was not run"
