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

"""The CI deploy of a rendered Snowflake trust, with the self-test as its gate (epic #1589).

``snowflake_trust_deploy.yml`` calls this after ``mirror-images``, ``sync-kit`` and ``render``.
Every statement still comes from :mod:`spcs_sql`, so CREATE SERVICE is granted to the services
role only for the length of each call and revoked in a ``finally`` exactly as ``make up`` does.
What this adds is what an unattended run needs and an operator does by eye:

``fl-gate``
    Exit 3 (hold) when the deploy would restart a running fl-client with a different spec. A
    restart kills an in-flight training round. Each deploy stamps every service with the SHA-256
    of the spec it was given (its COMMENT), so "would this change the fl-client" is a comparison
    of two hashes and never a parse of Snowflake's normalised spec. A running fl-client with no
    stamp counts as changed: unknown fails closed, as the hub's gate does. Released by
    re-dispatching with ``fl_quiesced=true``, the operator's statement that no round is running.

``deploy``
    Resume the pools, CREATE/ALTER the services, stamp them, resume suspended ones, wait for
    READY, run xnat-configure, smoke-cstore and the self-test as job services, and read each
    job's verdict from its status and logs. Whatever happens, it then schedules the trust to
    suspend itself (``--ttl-hours``) with a Snowflake task, so a failed or abandoned run cannot
    leave compute billing, and writes ``report.json`` and ``report.md``.

A GitHub OIDC token lives five minutes, and the waits here are longer, so every ``snow`` call is
preceded by a fresh token for the ``ci`` connection (no-op outside GitHub Actions).
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import spcs_sql  # noqa: E402

#: The jobs a deploy runs, in order. seed-orthanc is not one: the PACS seed lives on Orthanc's
#: block volume and survives every ALTER, so it is a first-install step (make seed-orthanc).
DEPLOY_JOBS = ("xnat-configure", "smoke-cstore", "selftest")
#: Attempts per job. xnat-configure makes one admin probe per run by design, and on a cold site
#: XNAT can still be initialising when xnat-web first reports READY; a second run is the remedy.
JOB_ATTEMPTS = {"xnat-configure": 2}
#: The self-test prints this line, and only this line, when every check passed.
SELFTEST_PASS = "SELFTEST ALL PASS"
#: The stamp's prefix in each service's COMMENT.
STAMP = "flip-spec-sha256:"
EXIT_HOLD = 3

Query = Callable[[str], list[dict[str, Any]]]
Run = Callable[[list[str]], None]


class DeployError(RuntimeError):
    pass


# --- talking to Snowflake ---------------------------------------------------------------------


def refresh_oidc_token(connection: str) -> None:
    """Put a fresh GitHub OIDC token where snow reads it for ``connection``. No-op elsewhere."""
    url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL")
    if not url:
        return
    audience = os.environ.get("SNOWFLAKE_AUDIENCE", "snowflakecomputing.com")
    request = urllib.request.Request(
        f"{url}&audience={audience}",
        headers={"Authorization": f"bearer {os.environ['ACTIONS_ID_TOKEN_REQUEST_TOKEN']}"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 (GitHub's own URL)
        token = json.load(response)["value"]
    print(f"::add-mask::{token}", flush=True)
    os.environ["SNOWFLAKE_TOKEN"] = token
    os.environ[f"SNOWFLAKE_CONNECTIONS_{connection.upper().replace('-', '_')}_TOKEN"] = token


def snow_query(snow: str, connection: str) -> Query:
    """One statement through ``snow sql --format json``; the rows of its (last) result."""

    def query(statement: str) -> list[dict[str, Any]]:
        refresh_oidc_token(connection)
        out = subprocess.run(
            [snow, "sql", "--stdin", "--enable-templating", "NONE", "--format", "json", "--connection", connection],
            input=statement + "\n",
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        rows = json.loads(out) if out.strip() else []
        if rows and isinstance(rows[0], list):
            rows = rows[-1]
        return [row for row in rows if isinstance(row, dict)]

    return query


def snow_run(snow: str, connection: str) -> Run:
    inner = spcs_sql.snow_runner(snow, connection)

    def run(statements: list[str]) -> None:
        refresh_oidc_token(connection)
        inner(statements)

    return run


def first_value(rows: list[dict[str, Any]]) -> str:
    return str(next(iter(rows[0].values()))) if rows and rows[0] else ""


# --- the FL gate ------------------------------------------------------------------------------


def spec_hash(directory: Path, service: dict[str, Any]) -> str:
    return hashlib.sha256((directory / service["spec_file"]).read_bytes()).hexdigest()


def long_running(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in manifest["services"] if s["kind"] != spcs_sql.JOB_KIND]


def running_services(query: Query, manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = query(f"SHOW SERVICES IN SCHEMA {manifest['database']}.APP;")
    return {str(row.get("name", "")).upper(): row for row in rows}


def fl_gate(directory: Path, manifest: dict[str, Any], query: Query) -> tuple[bool, list[str]]:
    """(hold, reasons). Hold when a RUNNING fl-client's stamp is not this spec's hash."""
    running = running_services(query, manifest)
    reasons = []
    for service in long_running(manifest):
        if not service["name"].startswith("fl-client"):
            continue
        row = running.get(service["sql_name"].rsplit(".", 1)[-1].upper())
        if row is None or str(row.get("status", "")).upper() in {"SUSPENDED", "SUSPENDING"}:
            continue  # nothing is training on a service that is not running
        comment = str(row.get("comment") or "")
        wanted = spec_hash(directory, service)
        if comment != STAMP + wanted:
            had = comment.removeprefix(STAMP)[:12] if comment.startswith(STAMP) else "no stamp"
            reasons.append(f"{service['name']} is running ({had}) and this deploy changes its spec ({wanted[:12]})")
    return bool(reasons), reasons


# --- the deploy -------------------------------------------------------------------------------


def _tolerant(statement: str) -> str:
    """A statement that may fail harmlessly (resume what is already running)."""
    return f"EXECUTE IMMEDIATE $$BEGIN {statement} EXCEPTION WHEN OTHER THEN NULL; END;$$;"


def sql_resume_pools(manifest: dict[str, Any]) -> list[str]:
    pools = sorted({s["pool"] for s in manifest["services"]})
    return [f"USE ROLE {manifest['role']};"] + [_tolerant(f"ALTER COMPUTE POOL {pool} RESUME;") for pool in pools]


def sql_stamp_and_resume(directory: Path, manifest: dict[str, Any]) -> list[str]:
    """As the services role: it owns the services, and SET COMMENT needs ownership."""
    statements = [f"USE ROLE {manifest['service_role']};"]
    for service in long_running(manifest):
        statements.append(
            f"ALTER SERVICE {service['sql_name']} SET COMMENT = '{STAMP}{spec_hash(directory, service)}';"
        )
        statements.append(_tolerant(f"ALTER SERVICE {service['sql_name']} RESUME;"))
    return statements


def sql_drop_job(manifest: dict[str, Any], name: str) -> list[str]:
    """A job service keeps its NAME after it ends, and EXECUTE JOB SERVICE refuses a taken one."""
    job = next(s for s in manifest["services"] if s["name"] == name)
    return [f"USE ROLE {manifest['role']};", f"DROP SERVICE IF EXISTS {job['sql_name']};"]


def expiry_task(manifest: dict[str, Any]) -> str:
    return f"{manifest['database']}.APP.FLIP_{manifest['code']}_CI_EXPIRE"


def sql_schedule_expiry(manifest: dict[str, Any], at: dt.datetime) -> list[str]:
    """A one-shot task that suspends every service and pool at ``at`` (UTC), then itself.

    Snowflake's own scheduler, not the CI runner, so it fires however the run ends. One name per
    trust: each deploy replaces it, so the window always counts from the latest deploy.
    """
    task = expiry_task(manifest)
    # Each statement guarded on its own (as cost.tf's nightly task), so one already suspended
    # does not stop the rest. Services before pools: a pool does not suspend under a service. The
    # task suspends itself last, since a day-and-month cron would otherwise fire again next year.
    targets = [f"SERVICE IF EXISTS {s['sql_name']}" for s in reversed(long_running(manifest))]
    targets += [f"COMPUTE POOL IF EXISTS {p}" for p in sorted(set(manifest["pools"].values()))]
    targets.append(f"TASK {task}")
    body = "\n    ".join(f"BEGIN ALTER {t} SUSPEND; EXCEPTION WHEN OTHER THEN NULL; END;" for t in targets)
    return [
        f"USE ROLE {manifest['role']};",
        f"CREATE OR REPLACE TASK {task}\n"
        f"  WAREHOUSE = {manifest['warehouse']}\n"
        f"  SCHEDULE = 'USING CRON {at.minute} {at.hour} {at.day} {at.month} * UTC'\n"
        f"  COMMENT = 'FLIP {manifest['code']}: CI test window ends {at:%Y-%m-%d %H:%M} UTC'\n"
        f"AS\n"
        f"  EXECUTE IMMEDIATE $$BEGIN\n"
        f"    {body}\n"
        f"  END;$$;",
        f"ALTER TASK {task} RESUME;",
    ]


def wait_ready(
    manifest: dict[str, Any], query: Query, timeout: float, sleep: Callable[[float], None]
) -> dict[str, str]:
    """Poll until every long-running service's containers are READY. Raise on timeout or FAILED."""
    deadline = time.monotonic() + timeout
    while True:
        states = {}
        for service in long_running(manifest):
            raw = first_value(query(f"SELECT SYSTEM$GET_SERVICE_STATUS('{service['sql_name']}');"))
            containers = json.loads(raw) if raw.strip().startswith("[") else []
            statuses = {str(c.get("status", "")).upper() for c in containers} or {"PENDING"}
            states[service["name"]] = "READY" if statuses == {"READY"} else ",".join(sorted(statuses))
        failed = {name: state for name, state in states.items() if "FAILED" in state}
        if failed:
            raise DeployError(f"service(s) failed to start: {failed}")
        if all(state == "READY" for state in states.values()):
            return states
        if time.monotonic() > deadline:
            raise DeployError(f"not READY after {timeout:.0f}s: {states}")
        sleep(20)


def run_job_to_end(
    directory: Path,
    manifest: dict[str, Any],
    name: str,
    run: Run,
    query: Query,
    timeout: float,
    sleep: Callable[[float], None],
) -> tuple[str, str]:
    """Execute one job (grant/revoke bracket from spcs_sql) and wait for DONE or FAILED."""
    run(sql_drop_job(manifest, name))
    spcs_sql.run_job(directory, manifest, name, run)
    job = next(s for s in manifest["services"] if s["name"] == name)
    deadline = time.monotonic() + timeout
    status = "PENDING"
    while time.monotonic() < deadline:
        raw = first_value(query(f"SELECT SYSTEM$GET_SERVICE_STATUS('{job['sql_name']}');"))
        containers = json.loads(raw) if raw.strip().startswith("[") else []
        statuses = {str(c.get("status", "")).upper() for c in containers}
        if statuses & {"DONE", "FAILED"}:
            status = "FAILED" if "FAILED" in statuses else "DONE"
            break
        sleep(15)
    else:
        status = "TIMEOUT"
    logs = first_value(query(f"SELECT SYSTEM$GET_SERVICE_LOGS('{job['sql_name']}', 0, '{name}', 500);"))
    return status, logs


def deploy(
    directory: Path,
    manifest: dict[str, Any],
    run: Run,
    query: Query,
    *,
    ttl_hours: float,
    ready_timeout: float = 1800,
    job_timeout: float = 1200,
    now: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "trust": manifest["code"],
        "env": manifest["env"],
        "images": sorted({s["image"] for s in manifest["services"]}),
        "steps": [],
        "ok": False,
    }

    def step(name: str, ok: bool, detail: str = "") -> None:
        report["steps"].append({"step": name, "ok": ok, "detail": detail})
        print(f"{'✅' if ok else '❌'} {name} {detail}".rstrip(), flush=True)

    try:
        run(sql_resume_pools(manifest))
        step("resume pools", True)
        spcs_sql.run_up(directory, manifest, run, frozenset(running_services(query, manifest)))
        run(sql_stamp_and_resume(directory, manifest))
        step("create/alter services", True)
        states = wait_ready(manifest, query, ready_timeout, sleep)
        step("services READY", True, f"{len(states)} service(s)")
        for name in DEPLOY_JOBS:
            for attempt in range(1, JOB_ATTEMPTS.get(name, 1) + 1):
                status, logs = run_job_to_end(directory, manifest, name, run, query, job_timeout, sleep)
                ok = status == "DONE" and (name != "selftest" or SELFTEST_PASS in logs)
                if ok:
                    break
                print(f"   {name} attempt {attempt}: {status}", flush=True)
            detail = status
            if name == "selftest":
                lines = [line for line in logs.splitlines() if line.startswith("SELFTEST ")]
                report["selftest"] = lines
                detail = f"{status}, {lines[-1] if lines else 'no verdict line'}"
            step(name, ok, detail)
            if not ok:
                raise DeployError(f"{name} did not pass ({detail})")
        report["ok"] = True
    except (DeployError, subprocess.CalledProcessError) as error:
        report["error"] = str(error)
    finally:
        until = (now() + dt.timedelta(hours=ttl_hours)).replace(second=0, microsecond=0)
        try:
            run(sql_schedule_expiry(manifest, until))
            report["suspends_at"] = f"{until:%Y-%m-%d %H:%M} UTC"
            step("expiry scheduled", True, report["suspends_at"])
        except subprocess.CalledProcessError as error:
            # The nightly suspend task (Terraform) still runs; say so rather than hide it.
            report["suspends_at"] = "not scheduled: the nightly suspend task is the only stop"
            step("expiry scheduled", False, str(error))
    return report


def report_markdown(report: dict[str, Any], run_url: str) -> str:
    verdict = "passed" if report["ok"] else "FAILED"
    lines = [
        f"Snowflake trust **{report['trust']}** ({report['env']}): deploy and self-test **{verdict}**.",
        "",
        f"The trust stays up for manual testing until **{report.get('suspends_at', 'unknown')}**, "
        "then suspends itself (services and compute pools).",
        "",
        "| Step | Result |",
        "|------|--------|",
    ]
    lines += [f"| {s['step']} | {'✅' if s['ok'] else '❌'} {s['detail']} |" for s in report["steps"]]
    if report.get("selftest"):
        lines += ["", "Self-test:", "```", *report["selftest"], "```"]
    if report.get("error"):
        lines += ["", f"Error: `{report['error']}`"]
    if run_url:
        lines += ["", f"Run: {run_url}"]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="CI deploy of a rendered Snowflake trust (epic #1589)")
    parser.add_argument("command", choices=["fl-gate", "deploy"])
    parser.add_argument("--dir", type=Path, required=True, help="Directory render_specs.py wrote")
    parser.add_argument("--snow", default="snow")
    parser.add_argument("--connection", default="ci")
    parser.add_argument("--ttl-hours", type=float, default=2.0, help="deploy: hours before the trust suspends")
    parser.add_argument("--report-dir", type=Path, default=Path("."), help="deploy: where report.{json,md} go")
    args = parser.parse_args()
    manifest = spcs_sql.load(args.dir / "manifest.json")
    query = snow_query(args.snow, args.connection)
    if args.command == "fl-gate":
        hold, reasons = fl_gate(args.dir, manifest, query)
        for reason in reasons:
            print(f"::warning title=FL gate::{reason}")
        print("HOLD: re-dispatch with fl_quiesced=true once no round is running." if hold else "FL gate: clear")
        return EXIT_HOLD if hold else 0
    report = deploy(args.dir, manifest, snow_run(args.snow, args.connection), query, ttl_hours=args.ttl_hours)
    args.report_dir.mkdir(parents=True, exist_ok=True)
    (args.report_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    run_url = ""
    if os.environ.get("GITHUB_RUN_ID"):
        server, repo = os.environ["GITHUB_SERVER_URL"], os.environ["GITHUB_REPOSITORY"]
        run_url = f"{server}/{repo}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
    (args.report_dir / "report.md").write_text(report_markdown(report, run_url))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
