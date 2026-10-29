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
    return [
        f"USE ROLE {manifest['role']};",
        f"{verb} CREATE SERVICE ON SCHEMA {manifest['database']}.APP {preposition} ROLE {manifest['service_role']};",
    ]


def sql_grant_create_service(manifest: dict[str, Any]) -> list[str]:
    """Give the services role CREATE SERVICE for the length of one deploy (FLIP#1578).

    A service's OAuth token carries the role that OWNS it, and SPCS refuses
    ``GRANT OWNERSHIP ON SERVICE``, so the only way to give services the narrow services
    role's token is to create them as that role. It must not keep the privilege: the
    fl-client runs researcher code with that token. ``run_up`` revokes it in a ``finally``.
    """
    return _create_service_grant(manifest, "GRANT")


def sql_revoke_create_service(manifest: dict[str, Any]) -> list[str]:
    return _create_service_grant(manifest, "REVOKE")


def sql_up(directory: Path, manifest: dict[str, Any], existing: frozenset[str] = frozenset()) -> list[str]:
    """CREATE the services that do not exist and ALTER the ones that do, as the services role.

    Snowflake has no ``CREATE OR ALTER SERVICE`` (the live account answers "syntax error ...
    unexpected 'OR'"), and ``CREATE OR REPLACE`` is not an option: replacing a service drops
    its block volumes (xnat-db's and Orthanc's databases) and every grant on it. So the
    caller names the services already there (``existing``, unqualified upper-case names
    from SHOW SERVICES): a new one is CREATEd, an existing one gets
    ``ALTER SERVICE ... FROM SPECIFICATION``, which upgrades it in place, plus a ``SET`` for
    the settings CREATE would have given it. The job service is only described.

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
    for service in manifest["services"]:
        body = _spec_body(directory, service)
        if service["kind"] == JOB_KIND:
            statements.append(
                f"-- {service['sql_name']} is a job service: executed on demand (EXECUTE JOB SERVICE),\n"
                f"-- never a running service. imaging-api starts it through the owner's-rights\n"
                f"-- procedure APP.CONVERT_SCAN (Terraform, FLIP#1578); {service['spec_file']} documents it."
            )
            continue
        if service["sql_name"].rsplit(".", 1)[-1].upper() in existing:
            statements += [
                f"ALTER SERVICE {service['sql_name']} FROM SPECIFICATION $$\n{body}$$;",
                f"ALTER SERVICE {service['sql_name']} SET QUERY_WAREHOUSE = {manifest['warehouse']} "
                "MIN_INSTANCES = 1 MAX_INSTANCES = 1;",
            ]
            continue
        statements.append(
            f"CREATE SERVICE {service['sql_name']}\n"
            f"  IN COMPUTE POOL {service['pool']}\n"
            f"  QUERY_WAREHOUSE = {manifest['warehouse']}\n"
            f"  MIN_INSTANCES = 1\n"
            f"  MAX_INSTANCES = 1\n"
            f"  FROM SPECIFICATION $$\n{body}$$;"
        )
    return statements


def sql_down(manifest: dict[str, Any]) -> list[str]:
    """DROP every service, newest first. Block volumes keep their snapshot on delete."""
    services = [s for s in manifest["services"] if s["kind"] != JOB_KIND]
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
    """Placeholder self-test: service status, then /health where a service has one.

    The real assertions (a cohort query, an image pull, an FL round) arrive with
    FLIP#1585. What this does today is the part that is cheap and still catches the
    common failure: a service that reports READY while its container never bound a port.
    ``SYSTEM$GET_SERVICE_STATUS`` is read through SQL, so the self-test needs no public
    endpoint and no egress - which is the whole reason it can run on a trial account.
    """
    statements = [
        "-- FLIP#1582 placeholder self-test. Real assertions land with FLIP#1585.",
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


def existing_services(snow: str, connection: str, manifest: dict[str, Any]) -> frozenset[str]:
    """The services already in the trust's APP schema, as unqualified upper-case names."""
    out = subprocess.run(
        [snow, "sql", "--stdin", "--enable-templating", "NONE", "--format", "json", "--connection", connection],
        input=f"SHOW SERVICES IN SCHEMA {manifest['database']}.APP;\n",
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    rows = json.loads(out) if out.strip() else []
    if rows and isinstance(rows[0], list):  # one result list per statement
        rows = rows[-1]
    return frozenset(str(row.get("name", "")).upper() for row in rows if isinstance(row, dict))


def run_up(
    directory: Path,
    manifest: dict[str, Any],
    run: Callable[[list[str]], None],
    existing: frozenset[str] = frozenset(),
) -> None:
    """Grant, create the services as the services role, and revoke whatever happened.

    The revoke sits in ``finally`` so a failed spec, a lost connection or Ctrl-C still
    leaves the services role without CREATE SERVICE. Rendering the statements first means
    a broken spec fails before anything is granted.
    """
    statements = sql_up(directory, manifest, existing)
    run(sql_grant_create_service(manifest))
    try:
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
    parser.add_argument("command", choices=sorted([*COMMANDS, "apply-up"]))
    parser.add_argument("--dir", type=Path, required=True, help="Directory render_specs.py wrote")
    parser.add_argument("--snow", default="snow", help="apply-up: the snow CLI")
    parser.add_argument("--connection", default="flip-test", help="apply-up: the snow connection")
    parser.add_argument("--dry-run", action="store_true", help="apply-up: print the SQL, run nothing")
    args = parser.parse_args()
    manifest = load(args.dir / "manifest.json")
    if args.command == "apply-up":
        # Executes, unlike the other commands: the grant must be revoked even when the
        # create fails, which a single piped script cannot guarantee.
        if args.dry_run:
            run_up(args.dir, manifest, print_runner)
        else:
            existing = existing_services(args.snow, args.connection, manifest)
            run_up(args.dir, manifest, snow_runner(args.snow, args.connection), existing)
        return
    for statement in COMMANDS[args.command](args.dir, manifest):
        print(statement)


if __name__ == "__main__":
    main()
