#!/usr/bin/env python3
#
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

"""Guards over the deploy SQL and the Makefile that runs it.

The SQL is emitted and piped, never built inside a recipe, which is what lets CI assert
the deploy path with no Snowflake account at all - the same credential-free property the
Terraform root's static checks rely on.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from conftest import SENTINELS

SNOWFLAKE_DIR = Path(__file__).resolve().parents[1]
if str(SNOWFLAKE_DIR) not in sys.path:
    sys.path.insert(0, str(SNOWFLAKE_DIR))

import spcs_sql  # noqa: E402

MAKEFILE = (SNOWFLAKE_DIR / "Makefile").read_text()


def test_up_uses_create_or_alter_not_replace(rendered: Path, manifest: dict) -> None:
    """CREATE OR REPLACE SERVICE drops the block volumes - xnat-db's and Orthanc's
    databases - and every grant. ALTER applies the new spec to the running service."""
    sql = "\n".join(spcs_sql.sql_up(rendered, manifest))
    assert "CREATE OR REPLACE SERVICE" not in sql
    assert sql.count("CREATE OR ALTER SERVICE") == len([s for s in manifest["services"] if s["kind"] != "job"])


def test_up_embeds_each_rendered_spec(rendered: Path, manifest: dict) -> None:
    sql = "\n".join(spcs_sql.sql_up(rendered, manifest))
    for service in manifest["services"]:
        if service["kind"] == "job":
            continue
        assert f"CREATE OR ALTER SERVICE {service['sql_name']}" in sql
        assert f"IN COMPUTE POOL {service['pool']}" in sql


def test_up_carries_no_secret_value(rendered: Path, manifest: dict) -> None:
    sql = "\n".join(spcs_sql.sql_up(rendered, manifest))
    for name, value in SENTINELS.items():
        assert value not in sql, f"{name} reached the deploy SQL"


def test_the_job_service_is_not_created_as_a_running_service(rendered: Path, manifest: dict) -> None:
    sql = "\n".join(spcs_sql.sql_up(rendered, manifest))
    assert "CREATE OR ALTER SERVICE FLIP_TRUST_GSTT.APP.DCM2NIIX" not in sql


def test_suspend_covers_the_compute_pools(manifest: dict) -> None:
    """A resource monitor does not cover SPCS compute pools: a suspended service on a
    running pool still bills every node it holds."""
    sql = "\n".join(spcs_sql.sql_suspend(manifest))
    for pool in set(manifest["pools"].values()):
        assert f"ALTER COMPUTE POOL IF EXISTS {pool} SUSPEND;" in sql
    assert "ALTER SERVICE IF EXISTS FLIP_TRUST_GSTT.APP.TRUST_API SUSPEND;" in sql


def test_down_drops_every_service(manifest: dict) -> None:
    sql = spcs_sql.sql_down(manifest)
    expected = [s for s in manifest["services"] if s["kind"] != "job"]
    assert len(sql) == len(expected)
    assert all(statement.startswith("DROP SERVICE IF EXISTS") for statement in sql)


def test_status_and_selftest_read_through_sql_only(manifest: dict) -> None:
    """Trial accounts have no External Network Access, so the self-test must not need an
    endpoint or any egress; SYSTEM$ functions are read over the SQL connection."""
    for statements in (spcs_sql.sql_status(manifest), spcs_sql.sql_selftest(manifest)):
        joined = "\n".join(statements)
        assert "SYSTEM$GET_SERVICE_STATUS" in joined
        assert "curl" not in joined
        assert "http" not in joined.lower()


def test_a_spec_that_would_break_the_sql_literal_is_refused(rendered: Path, manifest: dict, tmp_path: Path) -> None:
    import json
    import shutil

    shutil.copytree(rendered, tmp_path / "r")
    broken = tmp_path / "r" / "trust-api.yaml"
    broken.write_text(broken.read_text() + "\n# $$\n")
    local = json.loads((tmp_path / "r" / "manifest.json").read_text())
    with pytest.raises(SystemExit, match=re.escape("$$")):
        spcs_sql.sql_up(tmp_path / "r", local)


# ── the Makefile ─────────────────────────────────────────────────────────────


def test_every_required_target_exists() -> None:
    for target in ("render", "mirror-images", "sync-kit", "up", "down", "suspend", "status", "selftest", "test"):
        assert re.search(rf"^{re.escape(target)}:", MAKEFILE, re.M), f"missing target: {target}"


def test_dry_run_prints_instead_of_running() -> None:
    """The offline-testable deploy path: DRY_RUN swaps the snow pipe for `cat`."""
    assert "ifeq ($(strip $(DRY_RUN)),true)" in MAKEFILE
    assert "RUN_SQL = cat" in MAKEFILE
    assert "RUN_SQL = $(SNOW) sql --stdin --connection $(SNOW_CONNECTION)" in MAKEFILE


def test_prod_maps_to_the_kit_env_through_env_mode_mk() -> None:
    """One PROD map for the whole repo. A second copy is how the LZA tokens drifted."""
    assert "include $(REPO_ROOT)/deploy/env_mode.mk" in MAKEFILE
    assert "KIT_ENV := $(ENV)" in MAKEFILE


def test_mirror_pulls_amd64_only() -> None:
    """SPCS runs amd64 only; an arm64 workstation pulls arm64 by default and the service
    then fails to start with an exec-format error that names nothing useful."""
    assert "MIRROR_PLATFORM := linux/amd64" in MAKEFILE


def test_targets_requiring_a_kit_refuse_without_one() -> None:
    for target in ("render", "mirror-images", "sync-kit", "up", "down", "suspend", "status", "selftest"):
        body = re.search(rf"^{re.escape(target)}:(.*)$", MAKEFILE, re.M)
        assert body, f"{target} has no rule"
        assert "require-kit" in body.group(1), f"{target} must depend on require-kit"


def test_the_connection_name_is_a_variable_defaulting_to_the_test_account() -> None:
    assert "SNOW_CONNECTION ?= flip-test" in MAKEFILE
