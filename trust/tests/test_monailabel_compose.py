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
#
"""Production refuses a missing browser URL before any container is started."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

TRUST = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("public_url", [None, "", "https://labels.example.test"])
def test_production_requires_explicit_public_url(public_url):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI is needed (no daemon required)")
    env = dict(
        os.environ,
        DOCKER_TAG="test",
        NUM_AVAILABLE_GPUS="1",
        MONAI_LABEL_XNAT_ARCHIVE_DIR="/tmp/archive",
        XNAT_SERVICE_USER="test",
        XNAT_SERVICE_PASSWORD="test",  # pragma: allowlist secret — test fixture only
    )
    env.pop("MONAI_LABEL_PUBLIC_URL", None)
    if public_url is not None:
        env["MONAI_LABEL_PUBLIC_URL"] = public_url
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            "/dev/null",
            "-f",
            str(TRUST / "deploy/compose_trust.prod.monailabel.yml"),
            "config",
            "--format",
            "json",
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if not public_url:
        assert result.returncode != 0
        assert "MONAI_LABEL_PUBLIC_URL" in result.stderr
        assert "set it in the kit" in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        assert (
            json.loads(result.stdout)["services"]["monailabel"]["environment"]["MONAI_LABEL_PUBLIC_URL"] == public_url
        )
