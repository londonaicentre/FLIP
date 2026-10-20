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

"""Sync a registered FLIP trust *kit* into a Snowflake account (FLIP#1582).

The Snowflake twin of ``trust/deploy/helm/sync_k8s_kit.py``. It registers nothing with
the hub: registration happens once, centrally, and writes the kit file

    trust/.env.<CODE>.<env>

This script reads that kit and:

  1. Creates (and updates) the Snowflake SECRET objects of the naming contract in
     ``FLIP_TRUST_<CODE>.APP``, from the kit values, and grants READ on each to the
     service role. Terraform creates no secret values and never sees one.
  2. Pushes the FL participant kit onto the ``FL_KIT`` stage, under one ``net-<N>/``
     prefix per FL network, so the fl-client mounts only its own net's material.
  3. Pushes the trust's governance document (FLIP#1259) onto the ``GOVERNANCE`` stage,
     where data-access-api mounts it read-only.

**Secret values never appear in a command line, in a log, or on disk.** Every statement
is fed to ``snow sql`` on **stdin**; the process table, the shell history and this
script's own output carry object names only. ``--dry-run`` prints the SQL with every
secret value replaced by a placeholder, so the whole flow is testable offline.

Why ``CREATE SECRET IF NOT EXISTS`` + ``ALTER SECRET ... SET`` rather than
``CREATE OR REPLACE``: replacing a secret drops every grant on it, so the next deploy's
services would fail to start with a privilege error on an object that plainly exists.
The pair is idempotent and leaves the grants in place.

Usage:
  python3 sync_snowflake_kit.py --kit GSTT --env production --connection flip-test
  python3 sync_snowflake_kit.py --kit GSTT --env production --dry-run
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from kitfile import is_placeholder, kit_path, read_env_vars, resolve_env  # noqa: E402
from naming import SECRET_KIT_VAR, SECRETS, Names, NamingError  # noqa: E402

REPO_ROOT = HERE.parents[2]

#: Default Snowflake CLI connection. The test account; an operator names their own.
DEFAULT_CONNECTION = "flip-test"

#: What a dry run prints in place of a value. Never a prefix or a length of the real
#: one: a redaction that leaks the first characters is not a redaction.
REDACTED = "<redacted>"  # pragma: allowlist secret

#: Kit keys that must hold a real value before any of this is worth doing.
REQUIRED_KIT_KEYS = ("TRUST_API_KEY", "TRUST_INTERNAL_SERVICE_KEY", "AES_KEY_BASE64")


class SyncError(RuntimeError):
    """The kit cannot be synced into the account as asked."""


def sql_literal(value: str) -> str:
    """Quote a value as a Snowflake string literal.

    Backslash before quote: Snowflake honours backslash escapes inside single-quoted
    literals, so a value ending in ``\\`` would otherwise escape the closing quote and
    turn the rest of the statement into string data.
    """
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def secret_statements(names: Names, kit: dict[str, str]) -> tuple[list[str], list[str]]:
    """SQL for every secret the kit carries, and the names of the ones it does not.

    Returns:
        (statements, skipped) - the statements to run, and the secret object names with
        no real kit value. A skipped secret is not an error: a trust running no XNAT
        has no XNAT passwords, and the services that would map it are not deployed.
    """
    statements = [
        f"USE ROLE {names.admin_role};",
        f"USE DATABASE {names.database};",
        f"USE SCHEMA {names.app_schema};",
    ]
    skipped: list[str] = []
    for secret in SECRETS:
        value = kit.get(SECRET_KIT_VAR[secret], "")
        if is_placeholder(value):
            skipped.append(secret)
            continue
        qualified = names.secret(secret)
        statements += [
            f"CREATE SECRET IF NOT EXISTS {qualified} TYPE = GENERIC_STRING SECRET_STRING = {sql_literal('')};",
            f"ALTER SECRET {qualified} SET SECRET_STRING = {sql_literal(value)};",
            # The owner role creating a service needs READ on every secret it maps.
            f"GRANT READ ON SECRET {qualified} TO ROLE {names.service_role};",
        ]
    return statements, skipped


def redact(statements: list[str], kit: dict[str, str]) -> list[str]:
    """The same statements with every secret value replaced by :data:`REDACTED`.

    Used for the dry run and for anything printed. It substitutes the LITERAL forms
    (what actually appears in the SQL), so a value needing escaping is redacted too.
    """
    values = sorted(
        (sql_literal(kit[var]) for var in {SECRET_KIT_VAR[s] for s in SECRETS} if not is_placeholder(kit.get(var, ""))),
        key=len,
        reverse=True,
    )
    out = []
    for statement in statements:
        for literal in values:
            statement = statement.replace(literal, f"'{REDACTED}'")
        out.append(statement)
    return out


def run_sql(snow: str, connection: str, statements: list[str], kit: dict[str, str], dry_run: bool) -> None:
    """Execute statements through ``snow sql`` on stdin, or print them redacted.

    stdin, never ``-q``: an argument is visible in ``ps`` to every user on the host and
    is recorded by any command-logging the operator's shell or their CI runner does.
    """
    script = "\n".join(statements) + "\n"
    command = [snow, "sql", "--stdin", "--connection", connection]
    if dry_run:
        print("ⓘ  --dry-run: the SQL below would be piped to:")
        print(f"     {' '.join(command)}")
        for statement in redact(statements, kit):
            print(f"     {statement}")
        return
    result = subprocess.run(command, input=script, text=True, capture_output=True)
    if result.returncode != 0:
        # stderr may echo the failing statement, which may carry a value. Redact it.
        detail = "\n".join(redact(result.stderr.splitlines(), kit))
        raise SyncError(f"`snow sql` failed (exit {result.returncode}):\n{detail}")


def stage_push(snow: str, connection: str, source: Path, stage_ref: str, dry_run: bool) -> None:
    """Upload a file or a directory tree to an internal stage.

    No secret VALUE is involved - the FL kit's material and the governance document are
    files - so these may use arguments. The paths are still printed rather than the
    contents.
    """
    command = [
        snow,
        "stage",
        "copy",
        str(source),
        stage_ref,
        "--overwrite",
        "--connection",
        connection,
    ]
    if source.is_dir():
        # Appended, never inserted: the target stage reference is positional, and an
        # option spliced in front of it silently becomes the target.
        command.append("--recursive")
    if dry_run:
        print(f"     {' '.join(command)}")
        return
    result = subprocess.run(command, text=True, capture_output=True)
    if result.returncode != 0:
        raise SyncError(f"`snow stage copy` failed for {source} -> {stage_ref}:\n{result.stderr}")


def fl_nets_from_kit(kit: dict[str, str]) -> list[str]:
    raw = kit.get("FL_NETS", "").strip() or "1"
    return [part.strip() for part in raw.split(",") if part.strip()]


def push_fl_kit(snow: str, connection: str, names: Names, kit: dict[str, str], kit_dir: Path, dry_run: bool) -> None:
    """Push each FL net's participant material under its own ``net-<N>/`` prefix.

    The prefix is the whole point: the fl-client spec mounts ``@...FL_KIT/net-<N>``, so
    material pushed to the stage root, or to the wrong prefix, is either invisible to
    the client or visible to every client (which is the isolation rule this preserves).
    """
    if not kit_dir.exists():
        raise SyncError(
            f"FL kit directory {kit_dir} does not exist. Point --fl-kit-dir at the extracted kit "
            "(the directory holding net-<N>/), or pass --no-fl-kit to skip the push."
        )
    for net in fl_nets_from_kit(kit):
        source = kit_dir / f"net-{net}"
        if not source.exists():
            raise SyncError(f"FL kit has no net-{net} directory at {source} (FL_NETS names it)")
        stage_push(snow, connection, source, f"@{names.stage('FL_KIT')}/net-{net}/", dry_run)
        print(f"  ✓ FL kit net-{net} -> @{names.stage('FL_KIT')}/net-{net}/")


def push_governance(snow: str, connection: str, names: Names, kit: dict[str, str], dry_run: bool) -> Path | None:
    """Push the trust's governance document, when the kit names one.

    A relative ``ACCESS_POLICY_FILE`` resolves against ``trust/``, as Compose's
    ``--project-directory trust`` does for its own mount of the same file. Unreadable is
    a hard error rather than an omission: a trust would otherwise deploy clean and
    quietly keep the platform defaults the operator believes their rules replaced.
    """
    ref = kit.get("ACCESS_POLICY_FILE", "").strip()
    if not ref:
        print("  ⓘ  Kit names no ACCESS_POLICY_FILE - platform defaults apply, nothing pushed.")
        return None
    document = Path(ref)
    if not document.is_absolute():
        document = (REPO_ROOT / "trust" / document).resolve()
    if not document.is_file():
        raise SyncError(f"ACCESS_POLICY_FILE={ref!r} (resolved to {document}) is not a readable file")
    stage_push(snow, connection, document, f"@{names.stage('GOVERNANCE')}/governance.toml", dry_run)
    print(f"  ✓ Governance document -> @{names.stage('GOVERNANCE')}/governance.toml")
    return document


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync a FLIP trust kit into a Snowflake account")
    parser.add_argument("--kit", required=True, help="Trust CODE (e.g. GSTT)")
    parser.add_argument(
        "--env",
        default=None,
        help="Kit-file env token (trust/.env.<KIT>.<token>). Required when PROD is set; the "
        "Makefile injects it from deploy/env_mode.mk.",
    )
    parser.add_argument("--connection", default=DEFAULT_CONNECTION, help="snow CLI connection name")
    parser.add_argument("--snow", default="snow", help="snow CLI binary (tests point this at a fake)")
    parser.add_argument("--fl-kit-dir", type=Path, default=None, help="Local FL participant kit root")
    parser.add_argument("--no-fl-kit", action="store_true", help="Skip the FL kit stage push")
    parser.add_argument("--no-governance", action="store_true", help="Skip the governance document push")
    parser.add_argument("--kit-file", type=Path, default=None, help="Read the kit from this file instead")
    parser.add_argument("--dry-run", action="store_true", help="Print the redacted SQL and uploads; run nothing")
    args = parser.parse_args()

    env = resolve_env(args.env)
    try:
        names = Names(args.kit)
    except NamingError as e:
        print(f"❌ {e}")
        return 1

    path = args.kit_file or kit_path(REPO_ROOT, args.kit, env)
    print(f"🔧 Syncing Snowflake trust kit: {names.code}  (env={env})")
    print(f"   Kit file:   {path}")
    print(f"   Connection: {args.connection}")
    print(f"   Database:   {names.database}")
    print()
    if not path.exists():
        print(f"❌ Kit file not found: {path}")
        print("   Register the trust first:")
        print(f'     make new-trust TRUST_CODE={args.kit} TRUST_NAME="..."')
        print(f"     make -C deploy/providers/AWS register-trusts KIT={args.kit} PROD={env}")
        print(f"     make sync-trust-kit KIT={args.kit} PROD={env}")
        return 1

    kit = read_env_vars(path)
    missing = [key for key in REQUIRED_KIT_KEYS if is_placeholder(kit.get(key, ""))]
    if missing:
        print(f"❌ Kit {path.name} is missing real values for: {', '.join(missing)}")
        print("   These come from registration + the Hub-shared block.")
        return 1

    statements, skipped = secret_statements(names, kit)
    try:
        run_sql(args.snow, args.connection, statements, kit, args.dry_run)
        written = [s for s in SECRETS if s not in skipped]
        print(f"  ✓ {len(written)} SECRET object(s) created or updated (names only shown):")
        for secret in written:
            print(f"      {names.secret(secret)}")
        if skipped:
            print(f"  ⓘ  No kit value for: {', '.join(skipped)} - left untouched.")
        print()

        if not args.no_fl_kit:
            kit_dir = args.fl_kit_dir or Path(kit.get("FL_KIT_DIR", "").strip() or "/opt/flip/fl-kit")
            push_fl_kit(args.snow, args.connection, names, kit, kit_dir, args.dry_run)
        if not args.no_governance:
            push_governance(args.snow, args.connection, names, kit, args.dry_run)
    except SyncError as e:
        print(f"❌ {e}")
        return 1

    print()
    print("-" * 64)
    print("📦 Next:")
    print(f"     make -C trust/deploy/snowflake render KIT={args.kit} PROD={env}")
    print(f"     make -C trust/deploy/snowflake up KIT={args.kit} PROD={env}")
    print("-" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
