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

"""The fl-client sets UPLOADED_FEDERATED_DATA_BUCKET only when a bucket is configured.

It used to render ``UPLOADED_FEDERATED_DATA_BUCKET: ""`` always. flip-utils validates that
variable as an ``s3://`` URL, so the empty string crash-looped the client at import, whereas
leaving it unset takes flip-utils' default.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

CHART_DIR = Path(__file__).resolve().parents[1]
BUCKET_ENV = "UPLOADED_FEDERATED_DATA_BUCKET"


def _fl_client_env(*extra: str) -> str:
    out = subprocess.run(
        [
            "helm",
            "template",
            "t",
            str(CHART_DIR),
            "--set",
            "flClient.enabled=true",
            "--set",
            "flClient.kitHostPath=/opt/flip/fl-kit",
            *extra,
        ],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "HELM_NAMESPACE": "flip-trust"},
    )
    return out.stdout


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")
def test_fl_client_omits_bucket_env_when_unset():
    assert BUCKET_ENV not in _fl_client_env()


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")
def test_fl_client_passes_bucket_env_when_set():
    rendered = _fl_client_env("--set", "uploadedFederatedDataBucket=s3://example-bucket")
    assert re.search(rf"name: {BUCKET_ENV}\s+value: \"s3://example-bucket\"", rendered)
