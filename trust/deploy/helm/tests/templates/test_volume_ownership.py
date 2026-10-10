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

"""Grafana and Loki can write their volumes on a real cloud disk, rendered (FLIP#1390).

A fresh Azure Disk (or EBS volume) mounts root-owned; both images run as non-root users (Grafana
472, Loki 10001), so without an fsGroup each crash-loops on "permission denied". kind's local-path
volumes are world-writable, which hid it. Orthanc already does the same (fsGroup 999).
Skipped without helm.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")


def _pod_specs() -> dict[str, dict]:
    args = ["helm", "template", "trust-release", str(CHART_DIR), "--set", "flClient.kitHostPath=/opt/flip/fl-kit"]
    rendered = subprocess.run(args, capture_output=True, text=True, check=True, timeout=120).stdout
    specs = {}
    for doc in yaml.safe_load_all(rendered):
        if doc and doc.get("kind") in ("Deployment", "StatefulSet"):
            specs[doc["metadata"]["name"].removeprefix("trust-release-flip-trust-")] = doc["spec"]["template"]["spec"]
    return specs


@pytest.mark.parametrize(("component", "gid"), [("grafana", 472), ("loki", 10001)])
def test_the_volume_is_handed_to_the_images_user(component, gid):
    assert _pod_specs()[component].get("securityContext", {}).get("fsGroup") == gid


XNAT_DATA_SUBDIRS = ("archive", "build", "cache", "prearchive", "home/logs")


def test_xnat_web_gets_its_data_dirs_before_tomcat_starts():
    """xnat-web mounts five subPaths of its volume; Kubernetes creates a missing subPath root-owned
    0755, and XNAT runs as uid 1001, so it could write neither prearchive nor archive and aborted
    every C-STORE ("Peer aborted Association") on AKS. kind's world-writable volumes hid it."""
    spec = _pod_specs()["xnat-web"]
    init = next(c for c in spec["initContainers"] if c["name"] == "prepare-data-dirs")
    assert init["securityContext"]["runAsUser"] == 0
    assert "CHOWN" in init["securityContext"]["capabilities"]["add"]
    script = init["command"][-1]
    for sub in XNAT_DATA_SUBDIRS:
        assert f"/data/xnat/{sub}" in script, sub
    assert "chown 1001:1001" in script
    assert {m["name"] for m in init["volumeMounts"]} == {"data"}
    names = [c["name"] for c in spec["initContainers"]]
    assert names.index("prepare-data-dirs") == 0, "first, before anything writes there"
