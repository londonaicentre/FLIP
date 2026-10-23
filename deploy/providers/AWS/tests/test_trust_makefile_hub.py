# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""The trust-side Makefiles resolve every ``HUB`` value through ``deploy/env_mode.mk``.

The table lives once in ``deploy/env_mode.mk`` (pinned by ``test_env_mode.py``); these probes
run ``make`` for real against ``trust/Makefile`` and ``trust/xnat/Makefile`` to check they are
wired to it. The old kit names are probed in a scratch tree by ``tests/test_makefile.py``.
"""

import os
import subprocess
from pathlib import Path

import pytest
from make_probe import probe_make

REPO_ROOT = Path(__file__).resolve().parents[4]
TRUST_DIR = REPO_ROOT / "trust"
XNAT_DIR = TRUST_DIR / "xnat"

TRUST_ITEMS = ["$(ENV)", "$(__DCKR_SUFFIX)", "$(KIT_FILE)"]
XNAT_ITEMS = ["$(ENV)", "$(KIT_FILE)", "$(XNAT_DATA_DIR)", "$(STACK_FILES)"]
#: Every deployed HUB value — it is also the kit suffix (deploy/env_mode.mk's ENV).
DEPLOYED_HUBS = ["stag", "prod"]
#: The caller's shell must not choose the environment under test.
ENV = {k: v for k, v in os.environ.items() if k not in {"PROD", "HUB", "MAKEFLAGS"}}


def _probe(cwd: Path, items: list[str], hub: str, kit: str = "ZZPROBE") -> list[str]:
    # FL_BACKEND is normally read from the kit; with no kit the trust Makefile's
    # fl_backend.mk include refuses an empty value, so supply one for the probe.
    return probe_make(cwd, items, {"HUB": hub, "KIT": kit, "TRUST_NUM": "1", "FL_BACKEND": "nvflare"}, env=ENV)


@pytest.mark.parametrize("hub", DEPLOYED_HUBS)
def test_trust_makefile_maps_every_deployed_hub(hub: str) -> None:
    env, compose_suffix, kit_file = _probe(TRUST_DIR, TRUST_ITEMS, hub)
    assert env == hub
    assert compose_suffix == "prod", f"HUB={hub} must run the prod compose, got {compose_suffix}"
    # No kit exists for ZZPROBE, so the current name is the one named in the not-found error.
    assert kit_file == f".env.ZZPROBE.{hub}"


@pytest.mark.parametrize("hub", DEPLOYED_HUBS)
def test_xnat_makefile_maps_every_deployed_hub(hub: str) -> None:
    env, kit_file, data_dir, stack_files = _probe(XNAT_DIR, XNAT_ITEMS, hub)
    assert env == hub
    assert kit_file == f"../.env.ZZPROBE.{hub}"
    assert data_dir == "/opt/flip/xnat-trust1", f"HUB={hub} must use the deployed-environment XNAT data path"
    assert "docker-compose-stack.prod.yml" in stack_files
    assert "development" not in stack_files


@pytest.mark.parametrize(
    ("hub", "expected"), [("stag", 'if [ -n "stag" ]'), ("prod", 'if [ -n "prod" ]'), ("", 'if [ -n "" ]')]
)
def test_xnat_reset_recipe_branches_on_deployed_envs(hub: str, expected: str) -> None:
    """The recipe-level branch in ``xnat-reset`` follows env_mode.mk's IS_DEPLOYED too."""
    result = subprocess.run(
        ["make", "-n", "xnat-reset", f"HUB={hub}", "KIT=ZZPROBE", "TRUST_NUM=1", "FL_BACKEND=nvflare"],
        text=True,
        capture_output=True,
        cwd=XNAT_DIR,
        env=ENV,
        check=True,
    )
    assert expected in result.stdout


def test_development_paths_are_unchanged() -> None:
    env, compose_suffix, _ = _probe(TRUST_DIR, TRUST_ITEMS, "")
    assert (env, compose_suffix) == ("development", "development")
    env, _, data_dir, stack_files = _probe(XNAT_DIR, XNAT_ITEMS, "")
    assert env == "development"
    assert data_dir.endswith("/trust/xnat/xnat-data-trust1")
    assert "docker-compose-stack.development.yml" in stack_files
