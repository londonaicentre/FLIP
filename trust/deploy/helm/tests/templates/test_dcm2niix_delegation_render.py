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

"""The xnat-init Job runs the xnat-web image's configure-dcm2niix.sh, unless that script predates FLIP#1451.

A pin on an older image (``xnat.image.pin``, the kit's XNAT_TAG) hands the chart a Docker-only
script that dies on DATA_PATH, which ``helm upgrade`` reports only as a failed hook. Skipped without
helm; the chart workflow's helm-template job runs it.
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
    tmp_path.mkdir()
    (tmp_path / "configure-dcm2niix.sh").write_text(script)
    command = command.replace("cd /data/xnat/config", f"cd {tmp_path}")
    return subprocess.run(["bash", "-c", command], capture_output=True, text=True, timeout=60)


def test_the_container_runs_the_images_script_and_refuses_one_from_before_the_backend_switch(tmp_path: Path) -> None:
    current = _run(tmp_path / "current", _container_command(), 'echo "backend=${XNAT_CS_BACKEND:-unset}"\n')
    assert current.returncode == 0, current.stderr
    assert "backend=unset" in current.stdout, "the script was not run"

    # v0.12.0's script, reduced to the line that kills it under the chart.
    old = _run(tmp_path / "old", _container_command("xnat.image.pin=v0.12.0"), 'set -u\necho "DATA_PATH=$DATA_PATH"\n')
    assert old.returncode != 0
    assert f"{REPOSITORY}:v0.12.0 predates FLIP#1451" in old.stderr
    assert "xnat.image.pin" in old.stderr
    assert "DATA_PATH=" not in old.stdout, "the old script ran anyway"
