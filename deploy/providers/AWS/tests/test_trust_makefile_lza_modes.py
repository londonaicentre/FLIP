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
"""The trust-side Makefiles must resolve the LZA ``PROD`` values (FLIP#749).

``PROD=lza`` / ``PROD=lza-stag`` are one family with ``true`` / ``stag``: the same
production compose files and stack files, a deployed-environment data path, but their
own kit suffix — ``trust/.env.<CODE>.lza-prod`` / ``.lza-stag``, the files the AWS-side
``register-trusts`` writes. Every other helper that derives the environment from
``PROD`` learned both values in the 2026-08-28 sweep; ``trust/Makefile`` and
``trust/xnat/Makefile`` had not, so ``make -C trust up-trust KIT=X PROD=lza-stag`` fell
through to the DEVELOPMENT compose and the bare legacy ``.env.X`` — on a hub-admin
box that is a different trust's kit, silently started under the staging project name.

The table now lives once in ``deploy/env_mode.mk`` (pinned by ``test_env_mode.py``);
these probes run ``make`` for real against the two Makefiles to check they are wired
to it, with a KIT that has no kit file so the ``-include`` is inert.
"""

import subprocess
from pathlib import Path

import pytest
from make_probe import probe_make

REPO_ROOT = Path(__file__).resolve().parents[4]
TRUST_DIR = REPO_ROOT / "trust"
XNAT_DIR = TRUST_DIR / "xnat"

TRUST_ITEMS = ["$(ENV)", "$(__DCKR_SUFFIX)", "$(KIT_FILE)"]
XNAT_ITEMS = ["$(ENV)", "$(KIT_FILE)", "$(XNAT_DATA_DIR)", "$(STACK_FILES)"]
#: Every deployed PROD value and the kit suffix (ENV) it must map to — the table deploy/env_mode.mk owns.
DEPLOYED_PROD_VALUES = [("true", "production"), ("stag", "stag"), ("lza", "lza-prod"), ("lza-stag", "lza-stag")]


def _probe(cwd: Path, items: list[str], prod: str) -> list[str]:
    # FL_BACKEND is normally read from the kit; with no kit the trust Makefile's
    # fl_backend.mk include refuses an empty value, so supply one for the probe.
    return probe_make(cwd, items, {"PROD": prod, "KIT": "ZZPROBE", "TRUST_NUM": "1", "FL_BACKEND": "nvflare"})


@pytest.mark.parametrize(("prod", "env_suffix"), DEPLOYED_PROD_VALUES)
def test_trust_makefile_maps_every_deployed_prod_value(prod: str, env_suffix: str) -> None:
    env, compose_suffix, kit_file = _probe(TRUST_DIR, TRUST_ITEMS, prod)
    assert env == env_suffix, f"PROD={prod} must read kit .env.<CODE>.{env_suffix}, got .{env}"
    assert compose_suffix == "production", f"PROD={prod} must run the production compose, got {compose_suffix}"
    # No kit exists for ZZPROBE, so the legacy fallback wins — the suffix it TRIED first is ENV.
    assert kit_file == ".env.ZZPROBE"


@pytest.mark.parametrize(("prod", "env_suffix"), DEPLOYED_PROD_VALUES)
def test_xnat_makefile_maps_every_deployed_prod_value(prod: str, env_suffix: str) -> None:
    env, kit_file, data_dir, stack_files = _probe(XNAT_DIR, XNAT_ITEMS, prod)
    assert env == env_suffix
    assert kit_file == "../.env.ZZPROBE"
    assert data_dir == "/opt/flip/xnat-trust1", f"PROD={prod} must use the deployed-environment XNAT data path"
    assert "docker-compose-stack.production.yml" in stack_files
    assert "development" not in stack_files


@pytest.mark.parametrize(
    ("prod", "expected"),
    [("lza-stag", 'if [ -n "lza-stag" ]'), ("lza", 'if [ -n "lza" ]'), ("", 'if [ -n "" ]')],
)
def test_xnat_reset_recipe_branches_on_deployed_envs(prod: str, expected: str) -> None:
    """The recipe-level branch in ``xnat-reset`` follows env_mode.mk's IS_DEPLOYED too (it was a fourth
    ``true||stag``)."""
    result = subprocess.run(
        ["make", "-n", "xnat-reset", f"PROD={prod}", "KIT=ZZPROBE", "TRUST_NUM=1", "FL_BACKEND=nvflare"],
        text=True,
        capture_output=True,
        cwd=XNAT_DIR,
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
