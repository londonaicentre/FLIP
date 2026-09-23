#!/usr/bin/env -S uv run --script
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
"""A trust starts on its own slot's network, whatever the slot number (FLIP#1277).

Each trust attaches to ``$(INSTANCE_PREFIX)deploy_trust-network-<slot>``. ``create-networks``
used to make only slots 1 and 2 (the dev roster), and ``up-trust-ec2`` made none, so a trust
registered into slot 3+ — normal once a stag/prod slot pool grows — failed with ``network
deploy_trust-network-3 not found``. These guards dry-run the targets (``make -n``) against a
scratch kit in slot 9 and check the network the recipes would create.

Usage:
    uv run scripts/tests/test_trust_slot_networks.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

KIT = """TRUST_NAME=Scratch
TRUST_CODE=SCR
OMOP_DB_PORT=5499
PACS_UI_PORT=8099
XNAT_PORT=8198
XNAT_WEB_PORT=8199
TRUST_API_PORT=8029
IMAGING_API_PORT=8009
DATA_ACCESS_API_PORT=8019
GRAFANA_PORT=3009
LOKI_PORT=3109
ORTHANC_STORAGE_DIR=/nonexistent/orthanc
OMOP_DATA_DIR=/nonexistent/omop
XNAT_DATA_DIR=/nonexistent/xnat
FL_KIT_DIR=/nonexistent/fl-kit
BASE_IMAGES_DOWNLOAD_DIR=/nonexistent/images
NUM_AVAILABLE_GPUS=0
XNAT_ADMIN_USER=admin
XNAT_ADMIN_INITIAL_PASSWORD=x
XNAT_ADMIN_PASSWORD=x
XNAT_SERVICE_USER=svc
XNAT_SERVICE_PASSWORD=x
XNAT_DATASOURCE_PASSWORD=scratch-not-weak
XNAT_DATASOURCE_ADMIN_PASSWORD=scratch-not-weak
XNAT_ACTIVEMQ_PASSWORD=x
# ── Hub-shared (managed by register-trust / sync-trust-kits — do not edit) ──
AES_KEY_BASE64=x
CENTRAL_HUB_API_URL=https://hub.example/api
TRUST_API_KEY_HEADER=Authorization
FL_BACKEND=nvflare
FLOWER_KIT_DATE=20260101
FLARE_KIT_DATE=20260101
DOCKER_TAG=sha-badcff1
DOCKER_REGISTRY=ghcr.io/londonaicentre/
DOCKER_FL_TAG=sha-badcff1
DOCKER_FL_REGISTRY=ghcr.io/londonaicentre/
NLB_SUBDOMAIN=fl.example
FL_SERVER_PORT=8002
TRUST_API_KEY=x
TRUST_INTERNAL_SERVICE_KEY=x
FL_KIT_SLOT=Trust_9
FL_KIT_SLOT_NUMBER=9
EXPECTED_TRUST_ID=x
"""


def _dry_run(target: str, *args: str) -> str:
    """`make -n` a trust target in a scratch copy of the Makefiles, with the slot-9 kit in scope."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for rel in (
            "deploy/env_mode.mk",
            "deploy/fl_backend.mk",
            "deploy/instance.mk",
            "trust/Makefile",
            "trust/xnat/Makefile",
        ):
            dst = root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(REPO / rel, dst)
        shutil.copy(REPO / "trust" / "xnat" / ".env", root / "trust" / "xnat" / ".env")
        (root / "trust" / ".env.SCR.production").write_text(KIT)
        result = subprocess.run(
            ["make", "-n", "-C", str(root / "trust"), target, *args],
            capture_output=True,
            text=True,
            env={**os.environ, "PROD": "true"},
            timeout=60,
        )
        assert result.returncode == 0, f"make -n {target} failed:\n{result.stdout}\n{result.stderr}"
        return result.stdout


NET9 = "network create --driver overlay --attachable deploy_trust-network-9"


class SlotNetworks(unittest.TestCase):
    def test_create_networks_ensures_the_kit_slot(self):
        """up-trust depends on create-networks, so this is the compose-on-a-host path."""
        out = _dry_run("create-networks", "KIT=SCR", "PROD=true")
        assert NET9 in out, out

    def test_create_networks_without_a_kit_still_makes_the_dev_roster(self):
        out = _dry_run("create-networks", "PROD=true")
        for n in (1, 2):
            assert f"deploy_trust-network-{n}" in out, out
        assert "deploy_trust-network-9" not in out, out

    def test_up_trust_ec2_ensures_its_slot_before_the_compose(self):
        out = _dry_run("up-trust-ec2", "KIT=SCR", "PROD=true")
        assert NET9 in out, f"up-trust-ec2 does not create its slot network:\n{out}"
        assert out.index(NET9) < out.index(" up -d"), f"the network must exist before the compose starts:\n{out}"

    def test_the_per_slot_target_keeps_its_name(self):
        """Existing callers (and trust/xnat/tests/test_startup.py stubs) name create-networks-trust-1/2."""
        assert "deploy_trust-network-2" in _dry_run("create-networks-trust-2", "PROD=true")
        assert "deploy_trust-network-7" in _dry_run("create-networks-trust-7", "PROD=true")


if __name__ == "__main__":
    if not shutil.which("make"):
        print("make not installed — skipping", file=sys.stderr)
        sys.exit(0)
    unittest.main()
