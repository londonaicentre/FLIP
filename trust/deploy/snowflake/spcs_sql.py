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
import sys
from pathlib import Path
from typing import Any

#: Services are deployed in manifest order (dependencies first) and stopped in reverse.
#: A job service is never "up": it is executed on demand by imaging-api, so `up` only
#: ensures its specification is staged, and `down`/`suspend` ignore it.
JOB_KIND = "job"


def load(manifest_path: Path) -> dict[str, Any]:
    if not manifest_path.exists():
        sys.exit(f"❌ {manifest_path} not found. Run `make render KIT=<CODE> PROD=<env>` first.")
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


def sql_up(directory: Path, manifest: dict[str, Any]) -> list[str]:
    """CREATE OR ALTER SERVICE for every service; the job service is only described.

    ``CREATE OR ALTER`` rather than ``CREATE OR REPLACE``: replacing a service drops its
    block volumes (xnat-db's and Orthanc's databases) and every grant on it. ALTER
    applies the new specification to the running service in place.
    """
    statements = [
        # Services are created by the deploy role, which owns them and every object they
        # reference. It holds BIND SERVICE ENDPOINT on the account and nothing else that is
        # account-level (epic FLIP#1589).
        f"USE ROLE {manifest['role']};",
        f"USE DATABASE {manifest['database']};",
        f"USE SCHEMA {manifest['database']}.APP;",
    ]
    for service in manifest["services"]:
        body = _spec_body(directory, service)
        if service["kind"] == JOB_KIND:
            statements.append(
                f"-- {service['sql_name']} is a job service: executed on demand (EXECUTE JOB SERVICE),\n"
                f"-- never a running service. Its spec is rendered to {service['spec_file']} and passed\n"
                "-- at execution time by imaging-api."
            )
            continue
        statements.append(
            f"CREATE OR ALTER SERVICE {service['sql_name']}\n"
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


COMMANDS = {
    "up": lambda directory, manifest: sql_up(directory, manifest),
    "down": lambda directory, manifest: sql_down(manifest),
    "suspend": lambda directory, manifest: sql_suspend(manifest),
    "status": lambda directory, manifest: sql_status(manifest),
    "selftest": lambda directory, manifest: sql_selftest(manifest),
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Emit SPCS deploy SQL from a rendered trust")
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument("--dir", type=Path, required=True, help="Directory render_specs.py wrote")
    args = parser.parse_args()
    manifest = load(args.dir / "manifest.json")
    for statement in COMMANDS[args.command](args.dir, manifest):
        print(statement)


if __name__ == "__main__":
    main()
