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

"""The omop-vocab-load Job, rendered: it loads the bundle staged onto the node (FLIP#923).

No fetch, no bucket, no AWS settings; the S3 keys it used to take fail the render with directions.
Skipped without helm.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).resolve().parents[2]
STAGED = "omopDb.vocabLoad.hostPath=/opt/flip/omop-vocab"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")


def _helm(*sets: str) -> subprocess.CompletedProcess:
    args = ["helm", "template", "trust-release", str(CHART_DIR), "--set", "flClient.kitHostPath=/opt/flip/fl-kit"]
    for item in sets:
        args += ["--set", item]
    return subprocess.run(args, capture_output=True, text=True, timeout=120)


def _job(*sets: str) -> dict | None:
    result = _helm(*sets)
    assert result.returncode == 0, result.stderr
    for doc in yaml.safe_load_all(result.stdout):
        if doc and doc.get("kind") == "Job" and doc["metadata"]["name"].endswith("-omop-vocab-load"):
            return doc
    return None


@pytest.mark.parametrize("sets", [(), ("omopDb.vocabLoad.enabled=false", STAGED), ("omopDb.enabled=false", STAGED)])
def test_no_job_without_a_staged_bundle_or_with_the_load_off(sets):
    assert _job(*sets) is None


def test_the_job_loads_the_staged_folder_read_only_with_nothing_aws():
    job = _job(STAGED)
    assert job is not None
    pod = job["spec"]["template"]["spec"]
    assert "initContainers" not in pod
    (container,) = pod["containers"]
    assert "/flip/omop/load_core_vocab.sh /vocab /flip/omop/constraints.sql" in container["command"][2]
    assert container["volumeMounts"] == [{"name": "vocab", "mountPath": "/vocab", "readOnly": True}]
    assert pod["volumes"] == [{"name": "vocab", "hostPath": {"path": "/opt/flip/omop-vocab", "type": "Directory"}}]

    text = yaml.safe_dump(job)
    for aws in ("s3://", "aws ", "AWS_", ".aws"):
        assert aws not in text, f"{aws!r} in the vocab-load Job"


@pytest.mark.parametrize(
    "removed",
    [
        "s3Bucket=bucket",
        "s3Bucket=",
        "awsProfile=default",
        "hostAwsMount.enabled=false",
        "fetchResources.requests.cpu=1",
        "workDirSize=10Gi",
    ],
)
def test_a_removed_s3_key_fails_with_the_new_route(removed):
    result = _helm(f"omopDb.vocabLoad.{removed}")
    assert result.returncode != 0
    key = removed.split("=")[0].split(".")[0]
    assert f"omopDb.vocabLoad.{key} was removed" in result.stderr
    for hint in ("omopDb.vocabLoad.hostPath", "download-vocab", "stage-vocab"):
        assert hint in result.stderr


def test_a_relative_host_path_is_refused_by_the_schema():
    result = _helm("omopDb.vocabLoad.hostPath=omop-vocab")
    assert result.returncode != 0
    assert "hostPath" in result.stderr


def test_the_schema_and_defaults_carry_only_the_staged_route():
    schema = json.loads((CHART_DIR / "values.schema.json").read_text())
    vocab_schema = schema["properties"]["omopDb"]["properties"]["vocabLoad"]["properties"]
    defaults = yaml.safe_load((CHART_DIR / "values.yaml").read_text())["omopDb"]["vocabLoad"]
    assert set(vocab_schema) == set(defaults) == {"enabled", "hostPath", "loadResources"}
    assert defaults["hostPath"] == ""
