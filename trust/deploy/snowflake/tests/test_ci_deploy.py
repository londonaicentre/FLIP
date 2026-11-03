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

"""Guards over the CI deploy (ci_deploy.py): the FL gate, the self-test gate and the expiry.

A fake account stands in for Snowflake: it records every statement the deploy runs and answers
SHOW SERVICES and the status/log reads from a script, so each property is asserted on what would
reach the account.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from conftest import SENTINELS

SNOWFLAKE_DIR = Path(__file__).resolve().parents[1]
if str(SNOWFLAKE_DIR) not in sys.path:
    sys.path.insert(0, str(SNOWFLAKE_DIR))

import ci_deploy  # noqa: E402

NOW = dt.datetime(2026, 11, 3, 9, 41, 27, tzinfo=dt.UTC)


class FakeAccount:
    def __init__(self, manifest: dict, running: dict[str, dict] | None = None) -> None:
        self.manifest = manifest
        self.running = running or {}
        self.statements: list[str] = []
        self.job_status: dict[str, list[str]] = {}
        self.job_logs: dict[str, str] = {}
        self.service_status = "READY"
        self.fail_on: str | None = None

    def run(self, statements: list[str]) -> None:
        for statement in statements:
            if self.fail_on and self.fail_on in statement:
                import subprocess

                raise subprocess.CalledProcessError(1, ["snow"])
        self.statements += statements

    def _job(self, sql_name: str) -> str | None:
        jobs = (s for s in self.manifest["services"] if s["kind"] == "job")
        return next((s["name"] for s in jobs if s["sql_name"] == sql_name), None)

    def query(self, statement: str) -> list[dict[str, Any]]:
        self.statements.append(statement)
        if statement.startswith("SHOW SERVICES"):
            return [{"name": name, **row} for name, row in self.running.items()]
        sql_name = statement.split("('", 1)[1].split("'", 1)[0]
        job = self._job(sql_name)
        if "GET_SERVICE_LOGS" in statement:
            return [{"logs": self.job_logs.get(job or "", "")}]
        if job:
            queue = self.job_status.setdefault(job, ["DONE"])
            status = queue.pop(0) if len(queue) > 1 else queue[0]
        else:
            status = self.service_status
        return [{"status": json.dumps([{"status": status}])}]

    def sql(self) -> str:
        return "\n".join(self.statements)


def fl_client(manifest: dict) -> dict:
    return next(s for s in manifest["services"] if s["name"].startswith("fl-client"))


def unqualified(service: dict) -> str:
    return service["sql_name"].rsplit(".", 1)[-1].upper()


def passing(account: FakeAccount) -> FakeAccount:
    account.job_logs["selftest"] = "SELFTEST PASS health trust-api\nSELFTEST ALL PASS"
    return account


def run_deploy(rendered: Path, manifest: dict, account: FakeAccount, **kwargs) -> dict:
    return ci_deploy.deploy(
        rendered,
        manifest,
        account.run,
        account.query,
        ttl_hours=kwargs.pop("ttl_hours", 2),
        now=lambda: NOW,
        sleep=lambda _: None,
        **kwargs,
    )


# --- the FL gate ------------------------------------------------------------------------------


def test_the_gate_is_clear_on_a_first_deploy(rendered: Path, manifest: dict) -> None:
    hold, reasons = ci_deploy.fl_gate(rendered, manifest, FakeAccount(manifest).query)
    assert (hold, reasons) == (False, [])


def test_the_gate_holds_when_a_running_fl_client_would_change(rendered: Path, manifest: dict) -> None:
    client = fl_client(manifest)
    account = FakeAccount(manifest, {unqualified(client): {"status": "RUNNING", "comment": ci_deploy.STAMP + "0" * 64}})
    hold, reasons = ci_deploy.fl_gate(rendered, manifest, account.query)
    assert hold
    assert client["name"] in reasons[0]


def test_an_unstamped_running_fl_client_holds(rendered: Path, manifest: dict) -> None:
    """Unknown is not "unchanged": a client deployed by hand carries no stamp, so it holds."""
    account = FakeAccount(manifest, {unqualified(fl_client(manifest)): {"status": "RUNNING", "comment": ""}})
    assert ci_deploy.fl_gate(rendered, manifest, account.query)[0]


def test_the_same_spec_or_a_suspended_client_passes(rendered: Path, manifest: dict) -> None:
    client = fl_client(manifest)
    same = ci_deploy.STAMP + ci_deploy.spec_hash(rendered, client)
    for row in ({"status": "RUNNING", "comment": same}, {"status": "SUSPENDED", "comment": ""}):
        account = FakeAccount(manifest, {unqualified(client): row})
        assert ci_deploy.fl_gate(rendered, manifest, account.query) == (False, []), row


def test_only_the_fl_client_is_gated(rendered: Path, manifest: dict) -> None:
    """Restarting an API or XNAT costs a minute; restarting the fl-client costs a round."""
    others = {unqualified(s): {"status": "RUNNING", "comment": ""} for s in ci_deploy.long_running(manifest)}
    others.pop(unqualified(fl_client(manifest)))
    assert ci_deploy.fl_gate(rendered, manifest, FakeAccount(manifest, others).query) == (False, [])


# --- the deploy -------------------------------------------------------------------------------


def test_a_passing_deploy_runs_the_jobs_in_order_and_stamps_every_service(rendered: Path, manifest: dict) -> None:
    account = passing(FakeAccount(manifest))
    report = run_deploy(rendered, manifest, account)
    assert report["ok"], report
    sql = account.sql()
    executed = [line.split("NAME = ", 1)[1].split(".")[-1] for line in sql.splitlines() if line.startswith("  NAME = ")]
    # Not SEED_ORTHANC: the PACS seed lives on Orthanc's block volume, a first-install step.
    assert executed == ["XNAT_CONFIGURE", "SMOKE_CSTORE", "SELFTEST"], executed
    for service in ci_deploy.long_running(manifest):
        digest = ci_deploy.spec_hash(rendered, service)
        assert f"ALTER SERVICE {service['sql_name']} SET COMMENT = '{ci_deploy.STAMP}{digest}';" in sql, service["name"]


def test_the_self_test_is_the_gate(rendered: Path, manifest: dict) -> None:
    """A self-test job that ends DONE without the ALL PASS line is a failed deploy."""
    account = FakeAccount(manifest)
    account.job_logs["selftest"] = "SELFTEST PASS health trust-api\nSELFTEST FAIL omop backend\nSELFTEST 1 FAILED"
    report = run_deploy(rendered, manifest, account)
    assert not report["ok"]
    assert report["selftest"][-1] == "SELFTEST 1 FAILED"
    assert "selftest did not pass" in report["error"]


def test_a_failed_job_stops_the_deploy_and_xnat_configure_is_retried_once(rendered: Path, manifest: dict) -> None:
    account = passing(FakeAccount(manifest))
    account.job_status["xnat-configure"] = ["FAILED"]
    report = run_deploy(rendered, manifest, account)
    assert not report["ok"]
    assert account.sql().count("NAME = FLIP_TRUST_GSTT.APP.XNAT_CONFIGURE") == 2
    assert "NAME = FLIP_TRUST_GSTT.APP.SELFTEST" not in account.sql()


def test_a_service_that_fails_to_start_fails_the_deploy(rendered: Path, manifest: dict) -> None:
    account = passing(FakeAccount(manifest))
    account.service_status = "FAILED"
    report = run_deploy(rendered, manifest, account)
    assert not report["ok"]
    assert "failed to start" in report["error"]


@pytest.mark.parametrize("outcome", ["pass", "fail", "error"])
def test_the_expiry_is_scheduled_however_the_deploy_ends(rendered: Path, manifest: dict, outcome: str) -> None:
    account = passing(FakeAccount(manifest))
    if outcome == "fail":
        account.job_logs["selftest"] = "SELFTEST 2 FAILED"
    if outcome == "error":
        account.fail_on = "CREATE SERVICE"
    report = run_deploy(rendered, manifest, account)
    assert report["suspends_at"] == "2026-11-03 11:41 UTC"
    sql = account.sql()
    assert "CREATE OR REPLACE TASK FLIP_TRUST_GSTT.APP.FLIP_GSTT_CI_EXPIRE" in sql
    assert "SCHEDULE = 'USING CRON 41 11 3 11 * UTC'" in sql
    assert "ALTER TASK FLIP_TRUST_GSTT.APP.FLIP_GSTT_CI_EXPIRE RESUME;" in sql


def test_the_expiry_suspends_every_service_and_pool(manifest: dict) -> None:
    sql = "\n".join(ci_deploy.sql_schedule_expiry(manifest, NOW))
    guarded = "BEGIN ALTER {} SUSPEND; EXCEPTION WHEN OTHER THEN NULL; END;"
    for service in ci_deploy.long_running(manifest):
        assert guarded.format(f"SERVICE IF EXISTS {service['sql_name']}") in sql
    for pool in set(manifest["pools"].values()):
        assert guarded.format(f"COMPUTE POOL IF EXISTS {pool}") in sql
    assert sql.rfind("ALTER SERVICE") < sql.find("ALTER COMPUTE POOL"), "services before pools"
    assert guarded.format(f"TASK {ci_deploy.expiry_task(manifest)}") in sql, "a one-shot, not a yearly task"
    assert "DROP" not in sql, "the window ends in a suspend, never a drop: block volumes stay"


def test_the_stamp_runs_as_the_role_that_owns_the_services(rendered: Path, manifest: dict) -> None:
    statements = ci_deploy.sql_stamp_and_resume(rendered, manifest)
    assert statements[0] == f"USE ROLE {manifest['service_role']};"


def test_create_service_is_revoked_after_every_bracket(rendered: Path, manifest: dict) -> None:
    account = passing(FakeAccount(manifest))
    run_deploy(rendered, manifest, account)
    sql = account.sql()
    grants = sql.count("GRANT CREATE SERVICE ON SCHEMA")
    assert grants == sql.count("REVOKE CREATE SERVICE ON SCHEMA") == 1 + len(ci_deploy.DEPLOY_JOBS)
    assert sql.rfind("REVOKE CREATE SERVICE") > sql.rfind("EXECUTE JOB SERVICE")


def test_no_deploy_statement_uses_a_privileged_role_or_leaks_a_secret(rendered: Path, manifest: dict) -> None:
    account = passing(FakeAccount(manifest))
    report = run_deploy(rendered, manifest, account)
    text = account.sql() + json.dumps(report) + ci_deploy.report_markdown(report, "")
    for role in ("ACCOUNTADMIN", "SECURITYADMIN", "SYSADMIN", "USERADMIN"):
        assert f"USE ROLE {role}" not in text
    for secret in SENTINELS.values():
        assert secret not in text


def test_the_report_says_until_when_the_trust_stays_up(rendered: Path, manifest: dict) -> None:
    report = run_deploy(rendered, manifest, passing(FakeAccount(manifest)))
    text = ci_deploy.report_markdown(report, "https://example.invalid/run/1")
    assert "**passed**" in text
    assert "until **2026-11-03 11:41 UTC**" in text
    assert "SELFTEST ALL PASS" in text
