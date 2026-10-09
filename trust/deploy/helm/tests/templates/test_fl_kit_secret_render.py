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

"""The FL kit from a Kubernetes Secret instead of a node directory, rendered (FLIP#1390).

A managed node pool (AKS) replaces nodes at will, so nothing may live on one: the kit comes from a
Secret, and an init container lays it out in a pod volume the client can write (NVFLARE writes into
local/, chmods startup/ scripts and saves into transfer/; a Secret volume is read-only). The client
then mounts the same subdirectories it mounts from a hostPath. Skipped without helm.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")


def _fl_client(*sets: str) -> dict:
    args = ["helm", "template", "trust-release", str(CHART_DIR)]
    for item in sets:
        args += ["--set", item]
    rendered = subprocess.run(args, capture_output=True, text=True, check=True, timeout=120).stdout
    for doc in yaml.safe_load_all(rendered):
        if doc and doc.get("kind") == "Deployment" and doc["metadata"]["name"].endswith("fl-client-net-1"):
            return doc["spec"]["template"]["spec"]
    raise AssertionError("no fl-client Deployment rendered")


def _volumes(pod: dict) -> dict[str, dict]:
    return {v["name"]: v for v in pod["volumes"]}


def _init(pod: dict, name: str) -> dict:
    return next(c for c in pod.get("initContainers", []) if c["name"] == name)


def test_the_kit_comes_from_the_node_by_default():
    pod = _fl_client("flClient.kitHostPath=/opt/flip/fl-kit")
    assert _volumes(pod)["fl-client-kit"]["hostPath"]["path"] == "/opt/flip/fl-kit"
    assert "stage-fl-kit" not in {c["name"] for c in pod.get("initContainers", [])}


@pytest.mark.parametrize("backend", ["nvflare", "flower"])
def test_a_secret_kit_needs_no_node_path_and_is_staged_into_a_pod_volume(backend):
    pod = _fl_client("flClient.kit.source=secret", f"flBackend={backend}")
    volumes = _volumes(pod)
    assert "hostPath" not in volumes["fl-client-kit"]
    assert "emptyDir" in volumes["fl-client-kit"]
    assert volumes["fl-client-kit-secret"]["secret"]["secretName"] == "trust-release-flip-trust-fl-kit"
    stage = _init(pod, "stage-fl-kit")
    mounts = {m["name"]: m for m in stage["volumeMounts"]}
    assert mounts["fl-client-kit-secret"].get("readOnly") is True
    assert "fl-client-kit" in mounts
    client = next(c for c in pod["containers"] if c["name"] == "fl-client")
    subpaths = {m["subPath"] for m in client["volumeMounts"] if m["name"] == "fl-client-kit"}
    assert subpaths == ({"local", "startup", "transfer"} if backend == "nvflare" else {"certificates", "keys"})


def test_staging_hands_the_nvflare_kit_to_uid_1000():
    script = _init(_fl_client("flClient.kit.source=secret", "flBackend=nvflare"), "stage-fl-kit")["command"][-1]
    assert "chown -R 1000:1000" in script
    assert any(line.strip().startswith("mkdir -p") and "/kit/transfer" in line for line in script.splitlines()), (
        "NVFLARE saves the trained model into transfer/"
    )


def test_staging_lets_the_flower_supernode_read_its_key():
    script = _init(_fl_client("flClient.kit.source=secret", "flBackend=flower"), "stage-fl-kit")["command"][-1]
    assert "chgrp 49999" in script
    assert "chmod 0640" in script


def test_the_secret_name_can_be_chosen():
    pod = _fl_client("flClient.kit.source=secret", "flClient.kit.secretName=my-kit")
    assert _volumes(pod)["fl-client-kit-secret"]["secret"]["secretName"] == "my-kit"
