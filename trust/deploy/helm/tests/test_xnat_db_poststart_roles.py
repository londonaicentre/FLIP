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

"""The xnat-db postStart hook must reach the app role on either role layout (FLIP#1658).

An xnat-db volume carries the role layout of the chart that first initialised it. Current charts
leave ``POSTGRES_USER`` unset, so the superuser is ``postgres`` and ``XNAT.sql`` creates ``xnat``
as a separate application role. Charts up to the role split rendered ``POSTGRES_USER: <app
role>``, which the stock entrypoint honours only at the first initdb: those volumes hold a single
superuser named ``xnat`` and no ``postgres`` role at all.

The hook used to connect hardcoded as ``postgres`` and ``exit 1`` when that failed, so upgrading
such a trust crash-looped ``xnat-db`` and took XNAT, imaging-api and every DICOM path down with
it. It now probes both roles over the local socket and connects as whichever answers.

Two layers here, because neither is enough on its own:

* Text invariants over the hook body hold the credential-leak guard still: the four ``SET``
  statements are what stop a holder of the *current* app credential from reading the *new*
  password out of the log (PG12 does not redact ``ALTER ROLE ... PASSWORD``), so an edit that
  drops any of them must fail CI. ``log_statement = none`` and ``log_min_error_statement =
  panic`` cover the success and the error path; ``log_min_duration_statement = -1`` and
  ``log_transaction_sample_rate = 0`` cover the two settings that log a completed statement's
  text whatever ``log_statement`` says, and that the app role can arm for itself on the pre-split
  layout, where it is the superuser.
* The body actually runs, with stub ``psql`` / ``pg_isready`` / ``sleep`` executables on ``PATH``,
  over the three layouts a volume can be in: two roles, the pre-split single role, and neither.
  ``helm template`` cannot do this: the kind CI values disable XNAT, so the render never reaches
  this block.
"""

import shutil
import subprocess
from pathlib import Path

CHART_DIR = Path(__file__).resolve().parents[1]
XNAT_DB_TEMPLATE = CHART_DIR / "templates" / "xnat-db.yaml"

#: The one template expression the hook body carries; the render substitutes the kit's app role.
APP_ROLE_EXPR = "{{ .Values.xnat.db.datasourceUsername | quote }}"
APP_ROLE = "xnat"

#: A value the SQL must never carry in psql's argv: the hook hands it over through `printenv`.
PASSWORD = "poststart-test-password"  # pragma: allowlist secret

PSQL_STUB = """#!{python}
import os
import sys

args = sys.argv[1:]
role = args[args.index("-U") + 1]
log = os.environ["STUB_LOG"]

if "-tAc" in args:
    with open(log, "a") as handle:
        handle.write("probe {{role}}\\n".format(role=role))
    sys.exit(0 if role in os.environ.get("STUB_CONNECTABLE", "").split() else 1)

with open(log, "a") as handle:
    handle.write("alter {{role}}\\n".format(role=role))
with open(os.environ["STUB_ARGV"], "a") as handle:
    handle.write(repr(args) + "\\n")
with open(os.environ["STUB_SQL"], "a") as handle:
    handle.write(sys.stdin.read())
sys.exit(int(os.environ.get("STUB_ALTER_STATUS", "0")))
"""

STUB_SHELL = "#!/bin/sh\nexit 0\n"


def _hook_body() -> str:
    """Return the xnat-db postStart hook body with the one template expression resolved.

    The file is read as text rather than rendered, for the reason ``test_chart_secrets.py``
    gives: ``helm template`` only reaches the branches its values enable.

    Returns:
        str: The hook script, app role expression substituted, ready for ``/bin/sh``.
    """
    lines = XNAT_DB_TEMPLATE.read_text().splitlines()
    block_start = lines.index("                  - |")
    # YAML block-scalar indentation is set by the first non-empty content line.
    indent = len(lines[block_start + 1]) - len(lines[block_start + 1].lstrip())
    body = []
    for line in lines[block_start + 1 :]:
        if line.strip() and len(line) - len(line.lstrip()) < indent:
            break
        body.append(line[indent:] if line.strip() else "")

    script = "\n".join(body)
    assert APP_ROLE_EXPR in script, "the hook no longer templates the app role from values"
    script = script.replace(APP_ROLE_EXPR, f'"{APP_ROLE}"')
    assert "{{" not in script, "unresolved template expression left in the hook body"

    return script


def _run_hook(tmp_path: Path, connectable: str, alter_status: int = 0) -> tuple[subprocess.CompletedProcess, list[str]]:
    """Run the hook body against stubbed PostgreSQL clients.

    Args:
        tmp_path (Path): pytest's per-test directory.
        connectable (str): Space-separated roles the stub accepts a connection as.
        alter_status (int): Exit status the stub returns for the ALTER invocation.

    Returns:
        tuple[subprocess.CompletedProcess, list[str]]: The run, and the stub's role log.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = tmp_path / "hook.sh"
    script.write_text(_hook_body())

    (bin_dir / "psql").write_text(PSQL_STUB.format(python=shutil.which("python3") or "python"))
    (bin_dir / "psql").chmod(0o755)
    for name in ("pg_isready", "sleep"):
        (bin_dir / name).write_text(STUB_SHELL)
        (bin_dir / name).chmod(0o755)

    log = tmp_path / "roles.log"
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "STUB_LOG": str(log),
        "STUB_ARGV": str(tmp_path / "argv.log"),
        "STUB_SQL": str(tmp_path / "sql.log"),
        "STUB_CONNECTABLE": connectable,
        "STUB_ALTER_STATUS": str(alter_status),
        "XNAT_DATASOURCE_PASSWORD": PASSWORD,
    }
    result = subprocess.run(["sh", str(script)], capture_output=True, text=True, timeout=60, env=env)

    return result, log.read_text().splitlines() if log.exists() else []


def test_two_role_volume_connects_as_postgres(tmp_path: Path) -> None:
    """A current volume: ``postgres`` answers, and it is the role the ALTER runs as."""
    result, roles = _run_hook(tmp_path, connectable="postgres xnat")

    assert result.returncode == 0, result.stderr
    assert roles == ["probe postgres", "alter postgres"]

    argv = (tmp_path / "argv.log").read_text()
    assert "'-v', 'ON_ERROR_STOP=1'" in argv, "ON_ERROR_STOP must stay, so a failed ALTER fails the hook"
    assert "'-U', 'postgres'" in argv
    assert PASSWORD not in argv, "the password must reach psql through printenv, never through argv"

    sql = (tmp_path / "sql.log").read_text()
    assert "ALTER ROLE :\"u\" WITH PASSWORD :'pw';" in sql
    assert PASSWORD not in sql


def test_single_role_volume_falls_back_to_the_app_role(tmp_path: Path) -> None:
    """The pre-split volume: no ``postgres`` role, so the hook connects as the app role."""
    result, roles = _run_hook(tmp_path, connectable=APP_ROLE)

    assert result.returncode == 0, result.stderr
    assert roles == ["probe postgres", "probe xnat", "alter xnat"]
    assert "'-U', 'xnat'" in (tmp_path / "argv.log").read_text()


def test_no_usable_role_fails_the_hook_with_a_diagnosis(tmp_path: Path) -> None:
    """Neither role answers: fail loudly and name both, rather than skip the sync silently."""
    result, roles = _run_hook(tmp_path, connectable="")

    assert result.returncode != 0
    assert not [role for role in roles if role.startswith("alter")]
    assert "postgres" in result.stderr
    assert APP_ROLE in result.stderr
    assert "NOT synced" in result.stderr


def test_a_failing_alter_still_fails_the_hook(tmp_path: Path) -> None:
    """The fallback must not turn a real SQL failure (a missing app role) into a success."""
    result, roles = _run_hook(tmp_path, connectable=APP_ROLE, alter_status=1)

    assert result.returncode != 0
    assert roles == ["probe postgres", "probe xnat", "alter xnat"]
    assert "FAILED" in result.stderr
    assert "connected as xnat" in result.stderr
    assert "will not authenticate" in result.stderr


def test_hook_probes_roles_instead_of_assuming_postgres() -> None:
    """Text invariant: no unconditional ``-U postgres``, and both candidates are probed."""
    body = _hook_body()

    assert '-U "$role"' in body
    assert "-U postgres" not in body
    assert 'for candidate in postgres "$app_role"; do' in body
    assert "-tAc 'select 1'" in body, "the probe must be a read-only statement"
    assert ">/dev/null 2>&1" in body, "a probe that fails must stay quiet"


def test_credential_leak_guard_survives_the_fallback() -> None:
    """The four SET statements are what close the log-leak hole, whichever role connects."""
    body = _hook_body()

    assert "SET log_statement = none;" in body
    assert "SET log_min_error_statement = panic;" in body
    assert "SET log_min_duration_statement = -1;" in body, "duration logging must not echo the ALTER"
    assert "SET log_transaction_sample_rate = 0;" in body, "sampled logging must not echo the ALTER"
    assert "-v ON_ERROR_STOP=1" in body
    assert "<<-'EOSQL'" in body
    assert "\n\tEOSQL" in body
    assert "`printenv XNAT_DATASOURCE_PASSWORD`" in body
