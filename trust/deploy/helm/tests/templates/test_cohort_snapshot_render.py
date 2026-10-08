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


"""The approved-cohort membership store's wiring, asserted on the rendered chart (FLIP#857).

If the mount, the env var or the fsGroup goes missing, data-access-api cannot write the store, so no
project can be frozen and every approved one is refused — an outage that looks like a policy refusal,
not a deploy fault.
Needs helm, so the chart workflow's helm-template job runs it; the pytest job skips it.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")


def _data_access_documents(*sets: str) -> list[dict]:
    args = ["helm", "template", "trust-release", str(CHART_DIR), "--set", "flClient.kitHostPath=/opt/flip/fl-kit"]
    for item in sets:
        args += ["--set", item]
    args += ["--show-only", "templates/data-access-api.yaml"]
    rendered = subprocess.run(args, capture_output=True, text=True, check=True).stdout
    return [doc for doc in yaml.safe_load_all(rendered) if doc]


def _one(documents: list[dict], kind: str) -> dict:
    matches = [doc for doc in documents if doc["kind"] == kind]
    assert len(matches) == 1, [doc["kind"] for doc in documents]
    return matches[0]


def test_the_store_is_mounted_writable_and_configured_when_enabled():
    documents = _data_access_documents()
    deployment = _one(documents, "Deployment")
    pod = deployment["spec"]["template"]["spec"]
    container = next(c for c in pod["containers"] if c["name"] == "data-access-api")

    claim = _one(documents, "PersistentVolumeClaim")["metadata"]["name"]
    volume = next(v for v in pod["volumes"] if v["name"] == "cohort-snapshots")
    assert volume["persistentVolumeClaim"]["claimName"] == claim
    mount = next(m for m in container["volumeMounts"] if m["name"] == "cohort-snapshots")
    assert mount["mountPath"] == "/snapshots"
    assert not mount.get("readOnly", False)
    assert _one(documents, "ConfigMap")["data"]["COHORT_SNAPSHOT_DIR"] == "/snapshots"
    # The GHCR image runs as uid 1000: without fsGroup a fresh PVC is root-owned and unwritable.
    assert pod["securityContext"]["fsGroup"] == 1000
    # ReadWriteOnce: a surge pod on another node would wait on the volume forever.
    assert deployment["spec"]["strategy"]["type"] == "Recreate"


def test_nothing_of_the_store_renders_when_disabled():
    documents = _data_access_documents("dataAccessApi.snapshots.enabled=false")
    assert [doc for doc in documents if doc["kind"] == "PersistentVolumeClaim"] == []
    deployment = _one(documents, "Deployment")
    pod = deployment["spec"]["template"]["spec"]
    assert "cohort-snapshots" not in [v["name"] for v in pod.get("volumes", [])]
    assert "COHORT_SNAPSHOT_DIR" not in _one(documents, "ConfigMap")["data"]
    assert "strategy" not in deployment["spec"]


def test_a_read_write_many_store_keeps_rolling_updates():
    documents = _data_access_documents("dataAccessApi.snapshots.accessMode=ReadWriteMany")
    assert "strategy" not in _one(documents, "Deployment")["spec"]
