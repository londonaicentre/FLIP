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

"""Emit the SQL that deploys, stops, suspends or inspects a rendered trust.

Everything this module produces is printed, never executed: the Makefile pipes it into
``snow sql --stdin``, and ``DRY_RUN=true`` simply does not pipe. That split is what makes
the deploy path testable with no Snowflake account, no credentials and no network - the
same property the Terraform root's static CI relies on.

It reads ``manifest.json`` and the ``*.yaml`` files that ``render_specs.py`` wrote, so
the SQL can only ever name services that were actually rendered. No secret value passes
through here: a rendered spec carries secret OBJECT NAMES only.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

#: Services are deployed in manifest order (dependencies first) and stopped in reverse.
#: A job service is never "up": it is executed on demand by imaging-api, so `up` only
#: ensures its specification is staged, and `down`/`suspend` ignore it.
JOB_KIND = "job"


def load(manifest_path: Path) -> dict[str, Any]:
    if not manifest_path.exists():
        sys.exit(f"❌ {manifest_path} not found. Run `make render KIT=<CODE> HUB=<env>` first.")
    data: dict[str, Any] = json.loads(manifest_path.read_text())
    return data


def _spec_body(directory: Path, service: dict[str, Any]) -> str:
    """A rendered spec as a dollar-quoted SQL literal body.

    ``$$`` quoting is what ``FROM SPECIFICATION`` takes. A spec containing ``$$`` would
    end the literal early, so refuse rather than emit SQL that parses as something else.
    """
    text = (directory / service["spec_file"]).read_text()
    if "$$" in text:
        sys.exit(f"❌ {service['spec_file']} contains '$$', which would terminate the SQL literal early.")
    return text


def _create_service_grant(manifest: dict[str, Any], verb: str) -> list[str]:
    preposition = "TO" if verb == "GRANT" else "FROM"
    return [f"USE ROLE {manifest['role']};"] + [
        f"{verb} CREATE SERVICE ON SCHEMA {manifest['database']}.APP {preposition} ROLE {role};"
        for role in (manifest["service_role"], manifest["fl_role"])
    ]


def sql_grant_create_service(manifest: dict[str, Any]) -> list[str]:
    """Give the two service roles CREATE SERVICE for the length of one deploy (FLIP#1578).

    A service's OAuth token carries the role that OWNS it, and SPCS refuses
    ``GRANT OWNERSHIP ON SERVICE``, so the only way to give a service a narrow role's token
    is to create it as that role. Neither role may keep the privilege: the fl-client runs
    researcher code with its role's token. ``run_up`` revokes it in a ``finally``.
    """
    return _create_service_grant(manifest, "GRANT")


def sql_revoke_create_service(manifest: dict[str, Any]) -> list[str]:
    return _create_service_grant(manifest, "REVOKE")


def sql_up(
    directory: Path,
    manifest: dict[str, Any],
    existing: frozenset[str] = frozenset(),
    misowned: frozenset[str] = frozenset(),
) -> list[str]:
    """CREATE the services that do not exist and ALTER the ones that do, each as its own role.

    Snowflake has no ``CREATE OR ALTER SERVICE`` (the live account answers "syntax error ...
    unexpected 'OR'"), and ``CREATE OR REPLACE`` is not an option: replacing a service drops
    its block volumes (xnat-db's and Orthanc's databases) and every grant on it. So the
    caller names the services already there (``existing``, unqualified upper-case names
    from SHOW SERVICES): a new one is CREATEd, an existing one gets
    ``ALTER SERVICE ... FROM SPECIFICATION``, which upgrades it in place, a ``SET`` of its
    query warehouse and a ``RESUME`` (so ``up`` after ``suspend`` starts the node again). The
    job service is only described.

    A service's role is fixed at CREATE, so one in ``misowned`` (an fl-client created before
    it had a role of its own) is dropped and created again. It holds no block volume.

    Run only between ``sql_grant_create_service`` and ``sql_revoke_create_service``, which
    is what ``run_up`` (and so ``make up``) does.
    """
    statements = [
        # Created AS the services role, so each service's token is that role's and not the
        # deploy role's (FLIP#1578). The deploy user can assume it: the platform tier grants
        # it to the deploy role. Drop, suspend and status stay with the deploy role, which
        # inherits ownership of these services through that grant.
        f"USE ROLE {manifest['service_role']};",
        f"USE DATABASE {manifest['database']};",
        f"USE SCHEMA {manifest['database']}.APP;",
    ]
    role = manifest["service_role"]
    for service in manifest["services"]:
        body = _spec_body(directory, service)
        name = service["sql_name"].rsplit(".", 1)[-1].upper()
        if service["kind"] != JOB_KIND and name in misowned:
            statements += [f"USE ROLE {manifest['role']};", f"DROP SERVICE IF EXISTS {service['sql_name']};"]
            role = manifest["role"]
        if service["kind"] != JOB_KIND and service["role"] != role:
            role = service["role"]
            statements.append(f"USE ROLE {role};")
        # The FL role holds no warehouse: its services run no query.
        warehouse = service["role"] == manifest["service_role"]
        # An option of the service, not of its specification (Snowflake rejects it there).
        integrations = ", ".join(service.get("external_access_integrations", []))
        if service["kind"] == JOB_KIND:
            statements.append(
                f"-- {service['sql_name']} is a job service: executed on demand (EXECUTE JOB SERVICE),\n"
                f"-- never a running service. imaging-api starts it through the owner's-rights\n"
                f"-- procedure APP.CONVERT_SCAN (Terraform, FLIP#1578); {service['spec_file']} documents it."
            )
            continue
        if name in existing and name not in misowned:
            statements.append(f"ALTER SERVICE {service['sql_name']} FROM SPECIFICATION $$\n{body}$$;")
            if warehouse:
                # QUERY_WAREHOUSE only: instance counts are fixed at CREATE, and Snowflake
                # refuses any MIN/MAX_INSTANCES on a service with a block volume as
                # "scaling", even to the same value (live account, xnat-db).
                statements.append(f"ALTER SERVICE {service['sql_name']} SET QUERY_WAREHOUSE = {manifest['warehouse']};")
            # Set or cleared on every deploy, so turning egress off takes effect too.
            statements.append(
                f"ALTER SERVICE {service['sql_name']} SET EXTERNAL_ACCESS_INTEGRATIONS = ({integrations});"
                if integrations
                else f"ALTER SERVICE {service['sql_name']} UNSET EXTERNAL_ACCESS_INTEGRATIONS;"
            )
            # `make suspend` leaves the service suspended and ALTER does not wake it. The
            # pools resume by themselves (auto_resume) once a service asks for a node.
            statements.append(f"ALTER SERVICE {service['sql_name']} RESUME;")
            continue
        statements.append(
            f"CREATE SERVICE {service['sql_name']}\n"
            f"  IN COMPUTE POOL {service['pool']}\n"
            + (f"  QUERY_WAREHOUSE = {manifest['warehouse']}\n" if warehouse else "")
            + (f"  EXTERNAL_ACCESS_INTEGRATIONS = ({integrations})\n" if integrations else "")
            + f"  MIN_INSTANCES = 1\n"
            f"  MAX_INSTANCES = 1\n"
            f"  FROM SPECIFICATION $$\n{body}$$;"
        )
    return statements


def sql_run_job(directory: Path, manifest: dict[str, Any], name: str) -> list[str]:
    """EXECUTE JOB SERVICE for one rendered job service (FLIP#1583).

    The one-shot half of the node: xnat-configure, seed-orthanc, smoke-cstore and
    reconvert. Three properties are deliberate:

    * Run AS the services role, like the long-running services, so the job's own token
      carries the narrow role rather than the deploy role's. ``run_job`` grants
      CREATE SERVICE for the length of the call and revokes it in a ``finally``, exactly
      as ``run_up`` does, because EXECUTE JOB SERVICE needs the same privilege.
    * ``ASYNC = TRUE``. A statement has a 300 s timeout and configure-xnat.sh alone can
      take longer than that on a cold site, so a synchronous call would report a timeout
      for a job that is running correctly.
    * ``NAME =`` is given, so the job is addressable afterwards:
      SYSTEM$GET_SERVICE_STATUS and SYSTEM$GET_SERVICE_LOGS are the only way to read what
      it did. An anonymous job's output is unrecoverable once the call returns.

    Raises:
        SystemExit: ``name`` is not a job service in the manifest.
    """
    jobs = {service["name"]: service for service in manifest["services"] if service["kind"] == JOB_KIND}
    if name not in jobs:
        sys.exit(f"❌ {name!r} is not a job service in this manifest. Choose one of: {', '.join(sorted(jobs))}")
    service = jobs[name]
    body = _spec_body(directory, service)
    return [
        f"USE ROLE {manifest['service_role']};",
        f"USE DATABASE {manifest['database']};",
        f"USE SCHEMA {manifest['database']}.APP;",
        # A finished job keeps its name, and a second EXECUTE under it fails with "already
        # exists". The previous run's logs stay in the event table.
        f"DROP SERVICE IF EXISTS {service['sql_name']};",
        f"EXECUTE JOB SERVICE\n"
        f"  IN COMPUTE POOL {service['pool']}\n"
        f"  NAME = {service['sql_name']}\n"
        f"  ASYNC = TRUE\n"
        f"  QUERY_WAREHOUSE = {manifest['warehouse']}\n"
        f"  FROM SPECIFICATION $$\n{body}$$;",
        # Empty status means \"not scheduled yet\", not \"nothing happened\" (FLIP#1578).
        f"SELECT SYSTEM$GET_SERVICE_STATUS('{service['sql_name']}') AS {name.replace('-', '_')}_status;",
    ]


def sql_job_status(manifest: dict[str, Any], name: str) -> list[str]:
    """Status and the tail of the logs for one job service, by name."""
    jobs = {service["name"]: service for service in manifest["services"] if service["kind"] == JOB_KIND}
    if name not in jobs:
        sys.exit(f"❌ {name!r} is not a job service in this manifest. Choose one of: {', '.join(sorted(jobs))}")
    service = jobs[name]
    alias = name.replace("-", "_")
    return [
        f"SELECT SYSTEM$GET_SERVICE_STATUS('{service['sql_name']}') AS {alias}_status;",
        f"SELECT SYSTEM$GET_SERVICE_LOGS('{service['sql_name']}', 0, '{name}', 500) AS {alias}_logs;",
    ]


def sql_down(manifest: dict[str, Any]) -> list[str]:
    """DROP every service and finished job, newest first. Block volumes keep their snapshot."""
    services = manifest["services"]
    statements = [f"USE ROLE {manifest['role']};", f"USE SCHEMA {manifest['database']}.APP;"]
    return statements + [f"DROP SERVICE IF EXISTS {service['sql_name']};" for service in reversed(services)]


def sql_suspend(manifest: dict[str, Any]) -> list[str]:
    """Suspend every service AND every compute pool.

    The pools are the point. A resource monitor does not cover SPCS compute pools, so a
    suspended service on a running pool still bills every node it holds; this is the
    manual half of the cost control the epic requires alongside pool auto-suspend.
    """
    services = [s for s in manifest["services"] if s["kind"] != JOB_KIND]
    statements = [f"USE ROLE {manifest['role']};", f"USE SCHEMA {manifest['database']}.APP;"]
    statements += [f"ALTER SERVICE IF EXISTS {service['sql_name']} SUSPEND;" for service in reversed(services)]
    statements += [f"ALTER COMPUTE POOL IF EXISTS {pool} SUSPEND;" for pool in sorted(set(manifest["pools"].values()))]
    return statements


def sql_status(manifest: dict[str, Any]) -> list[str]:
    statements = [f"SHOW COMPUTE POOLS LIKE '{pool}';" for pool in sorted(set(manifest["pools"].values()))]
    for service in manifest["services"]:
        if service["kind"] == JOB_KIND:
            continue
        alias = service["name"].replace("-", "_")
        statements.append(f"SELECT SYSTEM$GET_SERVICE_STATUS('{service['sql_name']}') AS {alias}_status;")
    return statements


def sql_selftest(manifest: dict[str, Any]) -> list[str]:
    """The SQL half of the self-test: service status, then the tail of each API's log.

    Cheap, and it catches a service that reports READY while its container never bound a
    port. The assertions are the ``selftest`` job (scripts/selftest-spcs.sh), which
    ``make selftest`` runs next and waits for. ``SYSTEM$GET_SERVICE_STATUS`` is read through
    SQL, so this needs no public endpoint and no egress, and runs on a trial account.
    """
    statements = [
        "-- Every check below is a SQL read: no public endpoint, no egress, trial-account safe.",
    ]
    statements += sql_status(manifest)
    for service in manifest["services"]:
        has_health = any(
            endpoint.get("protocol") == "HTTP" and service["name"].endswith("-api")
            for endpoint in service.get("endpoints", [])
        )
        if not has_health:
            continue
        statements.append(
            f"SELECT SYSTEM$GET_SERVICE_LOGS('{service['sql_name']}', 0, '{service['name']}', 50) "
            f"AS {service['name'].replace('-', '_')}_logs;"
        )
    return statements


#: Job-service states that end the wait (SYSTEM$GET_SERVICE_STATUS).
JOB_PASSED = "DONE"
JOB_ENDED = frozenset({JOB_PASSED, "FAILED", "INTERNAL_ERROR", "DELETED"})


def wait_job(
    manifest: dict[str, Any],
    name: str,
    query: Callable[[str], list[dict[str, Any]]],
    timeout: float = 900,
    interval: float = 10,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Wait for a job service to end, print its log, and say whether it passed.

    ``EXECUTE JOB SERVICE ... ASYNC`` returns at once, so without this a target that runs a
    job (the self-test above all) exits 0 whatever the job found.
    """
    status_sql, logs_sql = sql_job_status(manifest, name)
    state = ""
    for _ in range(max(1, int(timeout // interval))):
        rows = query(status_sql)
        containers = json.loads(next(iter(rows[0].values()), "") or "[]") if rows else []
        state = containers[0].get("status", "") if containers else ""
        if state in JOB_ENDED:
            break
        sleep(interval)
    for row in query(logs_sql):
        print(next(iter(row.values()), ""))
    print(f"{'✅' if state == JOB_PASSED else '❌'} {name}: {state or 'no status'}")
    return state == JOB_PASSED


def snow_runner(snow: str, connection: str) -> Callable[[list[str]], None]:
    """Run one batch of statements through ``snow sql --stdin``; raise if it fails."""

    def run(statements: list[str]) -> None:
        subprocess.run(
            [snow, "sql", "--stdin", "--enable-templating", "NONE", "--connection", connection],
            input="\n".join(statements) + "\n",
            text=True,
            check=True,
        )

    return run


def print_runner(statements: list[str]) -> None:
    print("\n".join(statements))


def show_rows(snow: str, connection: str, statement: str) -> list[dict[str, Any]]:
    """Run one SHOW statement and return its rows."""
    out = subprocess.run(
        [snow, "sql", "--stdin", "--enable-templating", "NONE", "--format", "json", "--connection", connection],
        input=f"{statement}\n",
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    rows = json.loads(out) if out.strip() else []
    if rows and isinstance(rows[0], list):  # one result list per statement
        rows = rows[-1]
    return [row for row in rows if isinstance(row, dict)]


def existing_services(snow: str, connection: str, manifest: dict[str, Any]) -> tuple[frozenset[str], frozenset[str]]:
    """The services already in the trust's APP schema, and those under the wrong role.

    Both as unqualified upper-case names. The second set is the FL client services not owned
    by the FL role, which ``sql_up`` drops and creates again. Nothing else is ever dropped:
    xnat-db and Orthanc keep their databases on block volumes.
    """
    rows = show_rows(snow, connection, f"SHOW SERVICES IN SCHEMA {manifest['database']}.APP;")
    owners = {str(row.get("name", "")).upper(): str(row.get("owner", "")).upper() for row in rows}
    fl_clients = {
        s["sql_name"].rsplit(".", 1)[-1].upper() for s in manifest["services"] if s["role"] == manifest["fl_role"]
    }
    misowned = frozenset(name for name in fl_clients if owners.get(name, manifest["fl_role"]) != manifest["fl_role"])
    return frozenset(owners), misowned


def missing_integrations(snow: str, connection: str, manifest: dict[str, Any]) -> list[str]:
    """The egress integrations a spec attaches that the account does not have.

    Attaching one that does not exist fails CREATE SERVICE with a Snowflake error about the
    integration, which reads like a typo rather than "the platform tier was never applied with
    enable_hub_egress". Naming them here, before anything is granted, says which.
    """
    missing = []
    for name in manifest.get("external_access_integrations", []):
        rows = show_rows(snow, connection, f"SHOW INTEGRATIONS LIKE '{name}';")
        if not rows:
            missing.append(name)
    return missing


def run_up(
    directory: Path,
    manifest: dict[str, Any],
    run: Callable[[list[str]], None],
    existing: frozenset[str] = frozenset(),
    misowned: frozenset[str] = frozenset(),
) -> None:
    """Grant, create the services as the services role, and revoke whatever happened.

    The revoke sits in ``finally`` so a failed spec, a lost connection or Ctrl-C still
    leaves the services role without CREATE SERVICE. Rendering the statements first means
    a broken spec fails before anything is granted.
    """
    statements = sql_up(directory, manifest, existing, misowned)
    try:
        # Inside the try: if the second role's grant fails, the first is still revoked.
        run(sql_grant_create_service(manifest))
        run(statements)
    finally:
        run(sql_revoke_create_service(manifest))


def run_job(directory: Path, manifest: dict[str, Any], name: str, run: Callable[[list[str]], None]) -> None:
    """Grant, execute the job as the services role, and revoke whatever happened.

    The same shape as :func:`run_up`, and for the same reason: EXECUTE JOB SERVICE needs
    CREATE SERVICE, and the services role must not keep it, since the fl-client runs
    researcher code with that role's token.
    """
    statements = sql_run_job(directory, manifest, name)
    try:
        run(sql_grant_create_service(manifest))
        run(statements)
    finally:
        run(sql_revoke_create_service(manifest))


COMMANDS = {
    "up": lambda directory, manifest: sql_up(directory, manifest),
    "down": lambda directory, manifest: sql_down(manifest),
    "suspend": lambda directory, manifest: sql_suspend(manifest),
    "status": lambda directory, manifest: sql_status(manifest),
    "selftest": lambda directory, manifest: sql_selftest(manifest),
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Emit SPCS deploy SQL from a rendered trust")
    parser.add_argument("command", choices=sorted([*COMMANDS, "apply-up", "run-job", "job-status", "wait-job"]))
    parser.add_argument("--dir", type=Path, required=True, help="Directory render_specs.py wrote")
    parser.add_argument("--snow", default="snow", help="apply-up: the snow CLI")
    parser.add_argument("--connection", default="flip-test", help="apply-up: the snow connection")
    parser.add_argument("--dry-run", action="store_true", help="apply-up / run-job: print the SQL, run nothing")
    parser.add_argument("--service", default="", help="run-job / job-status / wait-job: the job service name")
    parser.add_argument("--timeout", type=float, default=900, help="wait-job: seconds to wait for the job to end")
    args = parser.parse_args()
    manifest = load(args.dir / "manifest.json")
    if args.command == "job-status":
        for statement in sql_job_status(manifest, args.service):
            print(statement)
        return
    if args.command == "wait-job":
        passed = wait_job(
            manifest, args.service, lambda sql: show_rows(args.snow, args.connection, sql), timeout=args.timeout
        )
        sys.exit(0 if passed else 1)
    if args.command == "run-job":
        # Executes, like apply-up and for the same reason: the grant must be revoked even
        # when the job fails, which a single piped script cannot guarantee.
        run_job(
            args.dir,
            manifest,
            args.service,
            print_runner if args.dry_run else snow_runner(args.snow, args.connection),
        )
        return
    if args.command == "apply-up":
        # Executes, unlike the other commands: the grant must be revoked even when the
        # create fails, which a single piped script cannot guarantee.
        if args.dry_run:
            run_up(args.dir, manifest, print_runner)
        else:
            missing = missing_integrations(args.snow, args.connection, manifest)
            if missing:
                sys.exit(
                    f"❌ The rendered specs attach {', '.join(missing)}, which this account does not have.\n"
                    "   Apply the platform root with enable_hub_egress = true first\n"
                    "   (trust/deploy/terraform/snowflake-platform), then re-run. Rendering with\n"
                    "   HUB_EGRESS=false deploys the trust without egress instead."
                )
            existing, misowned = existing_services(args.snow, args.connection, manifest)
            run_up(args.dir, manifest, snow_runner(args.snow, args.connection), existing, misowned)
        return
    for statement in COMMANDS[args.command](args.dir, manifest):
        print(statement)


if __name__ == "__main__":
    main()
