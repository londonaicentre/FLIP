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

"""Guards for the node's lifecycle: resume, re-running a job, and a job's verdict.

Each of these was found on the live account: `up` after `suspend` left every service
suspended, a second `smoke-cstore` failed on the first run's name, and `make selftest`
exited 0 whatever the self-test found.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SNOWFLAKE_DIR = Path(__file__).resolve().parents[1]
if str(SNOWFLAKE_DIR) not in sys.path:
    sys.path.insert(0, str(SNOWFLAKE_DIR))

import mirror_images  # noqa: E402
import spcs_sql  # noqa: E402

MAKEFILE = (SNOWFLAKE_DIR / "Makefile").read_text()


def test_up_resumes_a_service_that_was_suspended(rendered: Path, manifest: dict) -> None:
    sql = spcs_sql.sql_up(rendered, manifest, frozenset({"TRUST_API"}))
    assert "ALTER SERVICE FLIP_TRUST_GSTT.APP.TRUST_API RESUME;" in sql
    assert not any("XNAT_DB RESUME" in statement for statement in sql), "a new service is created running"


def test_a_job_drops_its_previous_run_before_executing(rendered: Path, manifest: dict) -> None:
    sql = spcs_sql.sql_run_job(rendered, manifest, "smoke-cstore")
    drop = sql.index("DROP SERVICE IF EXISTS FLIP_TRUST_GSTT.APP.SMOKE_CSTORE;")
    execute = next(i for i, statement in enumerate(sql) if statement.startswith("EXECUTE JOB SERVICE"))
    assert drop < execute


def _status(*states: str):
    """A query function answering SYSTEM$GET_SERVICE_STATUS with each state in turn."""
    remaining = list(states)

    def query(sql: str) -> list[dict]:
        if "GET_SERVICE_LOGS" in sql:
            return [{"LOGS": "SELFTEST ..."}]
        state = remaining.pop(0) if len(remaining) > 1 else remaining[0]
        return [{"STATUS": json.dumps([{"status": state, "message": ""}])}]

    return query


@pytest.mark.parametrize(
    ("states", "passed"),
    [(("PENDING", "READY", "DONE"), True), (("READY", "FAILED"), False), (("PENDING",), False)],
    ids=["done", "failed", "never finished"],
)
def test_wait_job_reports_the_jobs_verdict(manifest: dict, states: tuple[str, ...], passed: bool, capsys) -> None:
    result = spcs_sql.wait_job(manifest, "selftest", _status(*states), timeout=30, interval=10, sleep=lambda _: None)
    assert result is passed
    assert "SELFTEST ..." in capsys.readouterr().out, "the job's log is printed either way"


def test_wait_job_survives_a_job_that_is_not_scheduled_yet(manifest: dict) -> None:
    """An empty status means "not scheduled yet", not a failure to parse."""
    answers = iter([[{"STATUS": ""}], [{"STATUS": json.dumps([{"status": "DONE"}])}], []])
    assert spcs_sql.wait_job(manifest, "selftest", lambda sql: next(answers), sleep=lambda _: None)


def test_selftest_and_smoke_wait_for_their_verdict() -> None:
    for target in ("selftest", "smoke-cstore"):
        recipe = MAKEFILE.split(f"\n{target}: ", 1)[1].split("\n\n", 1)[0]
        assert f"$(call wait_job,{target})" in recipe, target


@pytest.mark.parametrize("target", ["down", "suspend", "status"])
def test_a_missing_manifest_fails_the_target(target: str, tmp_path: Path) -> None:
    """The SQL is piped into snow, and without pipefail the pipe's status was snow's: exit 0."""
    result = subprocess.run(
        ["make", "-C", str(SNOWFLAKE_DIR), target, "KIT=ZZ", "HUB=stag", "DRY_RUN=true"],
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    assert "manifest.json not found" in result.stdout + result.stderr


def test_the_fl_client_is_mirrored_at_the_fl_tag() -> None:
    kit = {"DOCKER_TAG": "v1.0.0", "DOCKER_FL_TAG": "v2.0.0"}
    assert mirror_images.source_tag(kit, "flare-fl-client") == "v2.0.0"
    assert mirror_images.source_tag({**kit, "FL_BACKEND": "flower"}, "flower-supernode") == "v2.0.0"
    assert mirror_images.source_tag(kit, "trust-api") == "v1.0.0"


# --- the FL client's own role ---------------------------------------------------------------------
#
# SPCS hands every container a session token for the role that owns its service. Created as the
# services role, the fl-client (researcher code) could read OMOP and write every stage.


def test_fl_clients_are_created_as_the_fl_role_and_everything_else_is_not(rendered: Path, manifest: dict) -> None:
    role = ""
    creators = {}
    for statement in spcs_sql.sql_up(rendered, manifest):
        if statement.startswith("USE ROLE "):
            role = statement.removeprefix("USE ROLE ").rstrip(";")
        elif statement.startswith("CREATE SERVICE "):
            creators[statement.split()[2].rsplit(".", 1)[-1]] = role
    assert creators.pop("FL_CLIENT_NET_1") == "FLIP_GSTT_FL"
    assert set(creators.values()) == {"FLIP_GSTT_SVC"}


def test_an_fl_client_is_given_no_warehouse(rendered: Path, manifest: dict) -> None:
    """The FL role holds none, and a service that names one it cannot use fails to create."""
    fl_client = "CREATE SERVICE FLIP_TRUST_GSTT.APP.FL_CLIENT"
    created = next(s for s in spcs_sql.sql_up(rendered, manifest) if s.startswith(fl_client))
    altered = spcs_sql.sql_up(rendered, manifest, frozenset({"FL_CLIENT_NET_1"}))
    assert "QUERY_WAREHOUSE" not in created
    assert not any("FL_CLIENT_NET_1 SET QUERY_WAREHOUSE" in statement for statement in altered)


def test_both_service_roles_lose_create_service_after_a_deploy(manifest: dict) -> None:
    for build, verb in ((spcs_sql.sql_grant_create_service, "GRANT"), (spcs_sql.sql_revoke_create_service, "REVOKE")):
        sql = build(manifest)
        for role in ("FLIP_GSTT_SVC", "FLIP_GSTT_FL"):
            assert any(s.startswith(f"{verb} CREATE SERVICE") and s.endswith(f"ROLE {role};") for s in sql), role


def test_an_fl_client_under_the_old_role_is_dropped_and_created_again(rendered: Path, manifest: dict) -> None:
    """A service's role is fixed at CREATE, so an ALTER would leave it with the old role's token."""
    sql = spcs_sql.sql_up(rendered, manifest, frozenset({"FL_CLIENT_NET_1"}), frozenset({"FL_CLIENT_NET_1"}))
    drop = sql.index("DROP SERVICE IF EXISTS FLIP_TRUST_GSTT.APP.FL_CLIENT_NET_1;")
    assert sql[drop - 1] == "USE ROLE FLIP_GSTT_DEPLOY;"
    assert sql[drop + 1] == "USE ROLE FLIP_GSTT_FL;"
    assert sql[drop + 2].startswith("CREATE SERVICE FLIP_TRUST_GSTT.APP.FL_CLIENT_NET_1")


def test_only_an_fl_client_is_ever_counted_as_misowned(manifest: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """xnat-db and Orthanc hold their databases on block volumes: never dropped by a deploy."""
    rows = [{"name": "FL_CLIENT_NET_1", "owner": "FLIP_GSTT_SVC"}, {"name": "XNAT_DB", "owner": "SOMEONE_ELSE"}]
    monkeypatch.setattr(spcs_sql, "show_rows", lambda *_: rows)
    existing, misowned = spcs_sql.existing_services("snow", "connection", manifest)
    assert existing == {"FL_CLIENT_NET_1", "XNAT_DB"}
    assert misowned == {"FL_CLIENT_NET_1"}


def test_the_fl_role_reads_only_the_secret_its_spec_maps() -> None:
    import sync_snowflake_kit
    from naming import SECRETS, Names

    sql = sync_snowflake_kit._object_statements(Names("GSTT"), dict.fromkeys(SECRETS, "'x'"))
    to_fl = [s for s in sql if s.endswith("TO ROLE FLIP_GSTT_FL;")]
    assert to_fl == ["GRANT READ ON SECRET FLIP_TRUST_GSTT.APP.TRUST_INTERNAL_SERVICE_KEY TO ROLE FLIP_GSTT_FL;"]
