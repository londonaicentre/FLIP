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
"""The site upgrade verbs are data-safe by construction (FLIP#1204).

`make -C trust up-trust` is a first-install verb: its `ensure-seeded` step re-seeds OMOP /
Orthanc whenever the seed markers differ from the kit (a `.data_version` bump replaces the
listed projects' rows and studies) and `up-xnat` runs `xnat-reset`
(`sudo rm -rf $XNAT_DATA_DIR`, archive and database). `upgrade-trust` /
`upgrade-onprem-trust` exist so a live site can move to a release without any of that, and
this guard dry-runs them (`make -n`) against a scratch kit to prove the recipe they expand
to never names a destructive step — the kind of regression that a later "let me just reuse
up-trust's prerequisites" refactor would introduce silently.

Usage:
    uv run scripts/tests/test_makefile_upgrade_targets.py
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

# A prod kit with every field the trust Makefiles read at parse time. Values are inert:
# the recipes are only ever printed (`make -n`), never run.
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

DESTRUCTIVE = (
    "xnat-reset",
    "ensure-seeded",
    "seed-omop",
    "seed-orthanc",
    "update-omop-data",
    "update-orthanc-data",
    "rm -rf",
    "down-trust",
)


def _dry_run(target: str, *extra: str, subdir: str = "trust") -> str:
    """`make -n` the target in a scratch copy of the repo's Makefiles with a scratch kit.

    ``subdir`` is where the target lives: ``trust`` for the trust-level verbs, ``.`` for the
    root operator entry points.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for rel in (
            "Makefile",
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
        # scripts/ paths are referenced relative to trust/, so the resolver must exist to
        # be printed; an empty file is enough for -n.
        (root / "scripts").mkdir()
        (root / "scripts" / "site_upgrade.py").write_text("")
        env = {**os.environ, "PROD": "true"}
        result = subprocess.run(
            ["make", "-n", "-C", str(root / subdir), target, "KIT=SCR", "PROD=true", *extra],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
        assert result.returncode == 0, f"make -n {target} failed:\n{result.stdout}\n{result.stderr}"
        return result.stdout


class UpgradeVerbs(unittest.TestCase):
    def test_upgrade_onprem_trust_is_gated_and_data_safe(self):
        out = _dry_run("upgrade-onprem-trust", "TAG=v0.6.0", "YES=1", subdir=".")
        assert "onboard_onprem_trust.py" in out, out  # the readiness checklist gates it
        assert "site_upgrade.py plan" in out, out
        assert "--tag v0.6.0" in out, out
        assert "--yes" in out, out
        assert "_upgrade-trust-apply" in out, out
        for step in DESTRUCTIVE:
            assert step not in out, f"{step!r} reached from upgrade-onprem-trust:\n{out}"

    def test_upgrade_trust_pulls_recreates_and_upgrades_xnat_without_reset(self):
        out = _dry_run("upgrade-trust", "TAG=v0.6.0", "YES=1")
        assert "site_upgrade.py plan" in out, out
        assert "_upgrade-trust-apply" in out, out
        for step in DESTRUCTIVE:
            assert step not in out, f"{step!r} reached from upgrade-trust:\n{out}"

    def test_apply_phase_names_the_tag_only_via_the_kit(self):
        """The apply sub-make re-includes the kit, so the tag it uses is whatever the resolver wrote."""
        out = _dry_run("_upgrade-trust-apply")
        assert "compose" in out, out
        assert " pull" in out, out
        assert " up -d" in out, out
        assert "upgrade-xnat" in out, out
        assert "sha-badcff1" in out, out  # printed from the kit, not from TAG=
        for step in DESTRUCTIVE:
            assert step not in out, f"{step!r} reached from _upgrade-trust-apply:\n{out}"

    def test_up_trust_is_still_the_first_install_verb(self):
        """The guard would be meaningless if up-trust had quietly stopped seeding and resetting."""
        out = _dry_run("up-trust")
        assert "ensure-seeded" in out, out
        assert "up-xnat" in out, out


if __name__ == "__main__":
    if not shutil.which("make"):
        print("make not installed — skipping", file=sys.stderr)
        sys.exit(0)
    unittest.main()
