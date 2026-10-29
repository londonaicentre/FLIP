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


def test_no_deploy_statement_uses_a_privileged_role(rendered: Path, manifest: dict) -> None:
    """FLIP_<CODE>_ADMIN is retired (epic FLIP#1589). Drop, suspend and the JIT grant run as the
    least-privilege workload role; the services are created as the services role (FLIP#1578)."""
    assert manifest["role"] == "FLIP_GSTT_DEPLOY"
    assert manifest["service_role"] == "FLIP_GSTT_SVC"
    for sql in (
        "\n".join(spcs_sql.sql_down(manifest)),
        "\n".join(spcs_sql.sql_suspend(manifest)),
        "\n".join(spcs_sql.sql_grant_create_service(manifest)),
        "\n".join(spcs_sql.sql_revoke_create_service(manifest)),
    ):
        assert "USE ROLE FLIP_GSTT_DEPLOY;" in sql
    for sql in [*(spcs_sql.sql_up(rendered, manifest)), *spcs_sql.sql_down(manifest)]:
        assert "FLIP_GSTT_ADMIN" not in sql, "the admin role is retired"
        assert "ACCOUNTADMIN" not in sql


# --- services are owned by the services role (FLIP#1578) -----------------------------------------
#
# A service's OAuth token carries the role that OWNS it, and SPCS refuses GRANT OWNERSHIP ON
# SERVICE. Created by the deploy role, every container (the fl-client running researcher code
# among them) would hold a token able to drop the trust database. So the services are created AS
# the services role, which holds CREATE SERVICE only for the length of `make up`.


def test_services_are_created_as_the_services_role(rendered: Path, manifest: dict) -> None:
    sql = spcs_sql.sql_up(rendered, manifest)
    assert sql[0] == "USE ROLE FLIP_GSTT_SVC;"
    assert not any(s.startswith("USE ROLE") for s in sql[1:]), "no switch back before the creates"
    assert "GRANT" not in "\n".join(sql), "sql_up grants nothing: run_up brackets it"


def test_the_grant_and_revoke_name_only_create_service_on_app(manifest: dict) -> None:
    assert spcs_sql.sql_grant_create_service(manifest)[1] == (
        "GRANT CREATE SERVICE ON SCHEMA FLIP_TRUST_GSTT.APP TO ROLE FLIP_GSTT_SVC;"
    )
    assert spcs_sql.sql_revoke_create_service(manifest)[1] == (
        "REVOKE CREATE SERVICE ON SCHEMA FLIP_TRUST_GSTT.APP FROM ROLE FLIP_GSTT_SVC;"
    )


class _Recorder:
    def __init__(self, fail_on: str | None = None, error: type[BaseException] = RuntimeError) -> None:
        self.batches: list[str] = []
        self.fail_on = fail_on
        self.error = error

    def __call__(self, statements: list[str]) -> None:
        batch = "\n".join(statements)
        self.batches.append(batch)
        if self.fail_on and self.fail_on in batch:
            raise self.error(f"failed on {self.fail_on}")


def _kind(batch: str) -> str:
    if "REVOKE CREATE SERVICE" in batch:
        return "revoke"
    return "grant" if "GRANT CREATE SERVICE" in batch else "up"


def _kinds(batches: list[str]) -> list[str]:
    return [_kind(batch) for batch in batches]


def test_run_up_grants_creates_then_revokes(rendered: Path, manifest: dict) -> None:
    run = _Recorder()
    spcs_sql.run_up(rendered, manifest, run)
    assert _kinds(run.batches) == ["grant", "up", "revoke"]


@pytest.mark.parametrize("error", [RuntimeError, KeyboardInterrupt])
def test_a_failed_or_interrupted_deploy_still_revokes(rendered: Path, manifest: dict, error) -> None:
    """A bad spec, a dropped connection or Ctrl-C must not leave the services role able to
    create containers."""
    run = _Recorder(fail_on="CREATE OR ALTER SERVICE", error=error)
    with pytest.raises(error):
        spcs_sql.run_up(rendered, manifest, run)
    assert _kinds(run.batches) == ["grant", "up", "revoke"]


def test_a_spec_that_cannot_render_fails_before_anything_is_granted(rendered: Path, manifest: dict) -> None:
    broken = dict(manifest, services=[*manifest["services"], {**manifest["services"][0], "spec_file": "missing.yaml"}])
    run = _Recorder()
    with pytest.raises(FileNotFoundError):
        spcs_sql.run_up(rendered, broken, run)
    assert run.batches == []


def test_make_up_goes_through_the_bracketed_runner() -> None:
    up = MAKEFILE[MAKEFILE.index("\nup:") : MAKEFILE.index("\ndown:")]
    assert "spcs_sql.py apply-up" in up
    assert "spcs_sql.py up " not in up, "piping sql_up straight into snow would skip the revoke"


def test_apply_up_dry_run_prints_the_bracketed_sql(rendered: Path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["spcs_sql.py", "apply-up", "--dir", str(rendered), "--dry-run"])
    spcs_sql.main()
    out = capsys.readouterr().out
    assert out.index("GRANT CREATE SERVICE") < out.index("CREATE OR ALTER SERVICE") < out.index("REVOKE CREATE SERVICE")


def test_the_hub_eai_is_not_attached_here(rendered: Path, manifest: dict) -> None:
    """Hub egress is tracked by FLIP#1586, and the integration belongs to the break-glass
    Terraform root. Nothing in the deploy SQL or in a rendered spec may reference it."""
    sql = "\n".join(spcs_sql.sql_up(rendered, manifest))
    assert "EXTERNAL_ACCESS_INTEGRATIONS" not in sql.upper()
    assert "HUB_EAI" not in sql.upper()
    for spec in sorted(rendered.glob("*.yaml")):
        assert "HUB_EAI" not in spec.read_text().upper(), spec.name


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
    drops = [statement for statement in sql if statement.startswith("DROP SERVICE IF EXISTS")]
    assert len(drops) == len(expected)
    assert all(statement.startswith(("USE ", "DROP SERVICE IF EXISTS")) for statement in sql)


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
    assert "RUN_SQL = $(SNOW) sql --stdin --enable-templating NONE --connection $(SNOW_CONNECTION)" in MAKEFILE


def test_hub_maps_to_the_kit_env_through_env_mode_mk() -> None:
    """One HUB map for the whole repo. A second copy is how the LZA tokens drifted."""
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


def test_no_tooling_emits_stop_all():
    """``ALTER COMPUTE POOL ... STOP ALL`` deletes every service in the pool, block volumes
    included, bypassing ``snapshotOnDelete`` (seen live: it dropped a Postgres service whose own
    DROP had just been refused). Suspend services, then the pool; never STOP ALL."""
    for path in [*SNOWFLAKE_DIR.glob("*.py"), SNOWFLAKE_DIR / "Makefile"]:
        assert "STOP ALL" not in path.read_text().upper(), path.name
