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
"""`make -n` a FLIP Makefile target in a scratch copy of the repo's Makefiles (FLIP#1204).

Shared by the Makefile tests that sit beside each Makefile (``tests/test_makefile.py``,
``trust/tests/test_makefile.py``, ``trust/xnat/tests/test_makefile.py``): they prove the site
upgrade verbs expand to a recipe that never names a destructive step. The copy keeps the real
Makefiles away from a developer's kit files; the scratch kit below stands in for one.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

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


def run_target(
    target: str,
    *extra: str,
    subdir: str = "trust",
    kit: str = KIT,
    dry: bool = False,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ``make [-n] <target>`` in a scratch copy of the repo's Makefiles with a scratch kit.

    ``subdir`` is where the target lives: ``trust`` for the trust-level verbs, ``.`` for the
    root operator entry points. ``env`` is layered over the caller's environment — how a test
    puts a stub ``docker`` first on PATH for a real (non ``-n``) run.
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
        (root / "trust" / ".env.SCR.production").write_text(kit)
        # scripts/ paths are referenced relative to trust/, so the resolver must exist to
        # be printed; an empty file is enough for -n.
        (root / "scripts").mkdir()
        (root / "scripts" / "site_upgrade.py").write_text("")
        return subprocess.run(
            ["make", *(["-n"] if dry else []), "-C", str(root / subdir), target, "KIT=SCR", "PROD=true", *extra],
            capture_output=True,
            text=True,
            env={**os.environ, "PROD": "true", **(env or {})},
            timeout=60,
        )


def dry_run(target: str, *extra: str, subdir: str = "trust", kit: str = KIT) -> str:
    """`make -n` the target (see :func:`run_target`); the recipe it would run, asserting make accepted it."""
    result = run_target(target, *extra, subdir=subdir, kit=kit, dry=True)
    assert result.returncode == 0, f"make -n {target} failed:\n{result.stdout}\n{result.stderr}"
    return result.stdout
