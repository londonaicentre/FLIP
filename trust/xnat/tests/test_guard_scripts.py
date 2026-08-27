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
"""
Behavioural tests for the XNAT weak-password guards (FLIP-PT-056).

``test_password_guards.py`` pins *what* the four layers consider weak, by parsing
their source. It cannot see *whether the guard still runs*: an errant ``exit 0``
in ``fail()``, a ``case`` block that drifts below the ``exec``, or a dropped
``set -e`` all leave the literals untouched and pass every static assertion.

These tests close that half by executing the two entrypoint guards as real
subprocesses across the reject / accept / retry matrix. No image build and no
Postgres are needed: both scripts are POSIX ``sh`` and reach for an external
command only on the accept path, so the few they do need (``docker-entrypoint.sh``
for xnat-db, ``psql`` for xnat-web) are stubbed onto ``PATH`` — the same
mock-bin idiom as ``deploy/providers/AWS/scripts/tests/test_add_fl_kits.sh``.

The weak values are imported from ``test_password_guards`` rather than restated,
so a value added to the canonical set is automatically exercised here too.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from test_password_guards import CANONICAL_WEAK_VALUES, GUARD_XNAT_DB, WAIT_FOR_POSTGRES

# A guard that stops refusing is a security regression; a guard that stops
# terminating is a boot hang. Both must fail the suite rather than wedge CI.
TIMEOUT_SECONDS = 30

# Deliberately minimal: only the stub directory plus the system binaries the
# scripts genuinely use (sleep, wc). Inheriting the caller's PATH would let a
# real psql on a developer's machine answer instead of the stub, so the suite
# would behave differently locally and in CI.
BASE_PATH = "/usr/bin:/bin"

# Accept-path fixtures. Distinct from each other because xnat-db refuses a
# superuser password that equals the app-role password.
STRONG_PASSWORD = "n0t-a-weak-value-app"  # pragma: allowlist secret
STRONG_ADMIN_PASSWORD = "n0t-a-weak-value-admin"  # pragma: allowlist secret

# A username the weak list does not contain, for the password == username arms
# (using the shipped `xnat` username would be caught by the weak arm first, so it
# could not tell the two branches apart).
SERVICE_USERNAME = "xnat-service-account"


def _resolve_shells() -> list[tuple[str, ...]]:
    """Find the POSIX shells available to run the guards under.

    The two guards ship in different base images — xnat-web is Debian/Tomcat
    (``/bin/sh`` is dash) and xnat-db is Alpine (``/bin/sh`` is busybox ash) — and
    ``eval``, ``case`` and quoting are exactly where those two diverge. Run every
    scenario under each shell present rather than trusting one to stand for both.

    Returns:
        list[tuple[str, ...]]: Argument vectors that invoke each available shell.
    """
    resolved = []
    for candidate in (("sh",), ("busybox", "sh")):
        binary = shutil.which(candidate[0])
        if binary is not None:
            resolved.append((binary, *candidate[1:]))
    return resolved


SHELLS = _resolve_shells()
SHELL_IDS = ["-".join([Path(shell[0]).name, *shell[1:]]) for shell in SHELLS]

assert SHELLS, "no POSIX shell found to run the guard scripts under"


def _write_stub(stub_bin: Path, name: str, body: str) -> None:
    """Install an executable ``sh`` stub onto the mock-bin directory.

    Args:
        stub_bin (Path): Directory prepended to the child process's ``PATH``.
        name (str): Command name the script under test will resolve.
        body (str): Shell body of the stub.
    """
    script = stub_bin / name
    script.write_text(f"#!/bin/sh\n{body}\n")
    script.chmod(0o755)


def _install_argv_recorder(stub_bin: Path, name: str, record: Path) -> None:
    """Install a stub that records the argument vector it was handed.

    Writes ``$#`` followed by one line per argument, so a flattened or re-split
    vector (the ``exec $cmd`` bug this PR fixed) is visible as a different line
    count rather than the same text.

    Args:
        stub_bin (Path): Mock-bin directory.
        name (str): Command name to install as.
        record (Path): File the stub writes the vector to.
    """
    _write_stub(stub_bin, name, f'printf "%s\\n" "$#" "$@" > {shlex.quote(str(record))}')


def _install_psql_stub(stub_bin: Path, calls: Path, *, message: str = "", fail_times: int = 0) -> None:
    """Install a ``psql`` stub that records every invocation.

    Args:
        stub_bin (Path): Mock-bin directory.
        calls (Path): File the stub appends one line to per invocation. Absent
            when the stub was never reached, which is how the ordering
            assertions prove the guard runs before any database contact.
        message (str): Written to stderr while the stub is still failing;
            ``wait-for-postgres.sh`` classifies the retry-vs-reject split on it.
        fail_times (int): Leading invocations that exit non-zero. Negative fails
            every time.
    """
    quoted_calls = shlex.quote(str(calls))
    _write_stub(
        stub_bin,
        "psql",
        f"echo call >> {quoted_calls}\n"
        f"attempts=$(wc -l < {quoted_calls})\n"
        f'if [ {fail_times} -lt 0 ] || [ "$attempts" -le {fail_times} ]; then\n'
        f"  >&2 echo {shlex.quote(message)}\n"
        f"  exit 1\n"
        f"fi\n"
        f"exit 0",
    )


def _run(
    script: Path,
    env: dict[str, str],
    *,
    shell: Sequence[str],
    stub_bin: Path,
    argv: Sequence[str] = (),
) -> subprocess.CompletedProcess[str]:
    """Execute a guard script under an explicit shell with a stubbed ``PATH``.

    Args:
        script (Path): The guard script under test.
        env (dict[str, str]): The child's environment, minus ``PATH``.
        shell (Sequence[str]): Argument vector invoking the shell.
        stub_bin (Path): Mock-bin directory, prepended to ``PATH``.
        argv (Sequence[str]): Arguments passed after the script path.

    Returns:
        subprocess.CompletedProcess[str]: The finished process, output captured.
    """
    return subprocess.run(
        [*shell, str(script), *argv],
        env={"PATH": f"{stub_bin}{os.pathsep}{BASE_PATH}", **env},
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
        check=False,
    )


@pytest.fixture
def stub_bin(tmp_path: Path) -> Path:
    """Create the mock-bin directory prepended to the child's ``PATH``.

    Args:
        tmp_path (Path): pytest per-test temporary directory.

    Returns:
        Path: The (empty) mock-bin directory.
    """
    path = tmp_path / "bin"
    path.mkdir()
    return path


def _xnat_db_env(**overrides: str) -> dict[str, str]:
    """Build a passing xnat-db environment, before any override.

    Args:
        **overrides (str): Values replacing the defaults.

    Returns:
        dict[str, str]: The environment for ``guard-xnat-db-passwords.sh``.
    """
    return {
        "POSTGRES_PASSWORD": STRONG_ADMIN_PASSWORD,
        "XNAT_DATASOURCE_PASSWORD": STRONG_PASSWORD,
        "XNAT_DATASOURCE_USERNAME": SERVICE_USERNAME,
        **overrides,
    }


def _xnat_web_env(**overrides: str) -> dict[str, str]:
    """Build a passing xnat-web environment, before any override.

    Args:
        **overrides (str): Values replacing the defaults.

    Returns:
        dict[str, str]: The environment for ``wait-for-postgres.sh``.
    """
    return {
        "XNAT_DATASOURCE_USERNAME": "xnat",
        "XNAT_DATASOURCE_PASSWORD": STRONG_PASSWORD,
        "XNAT_DATASOURCE_NAME": "xnat",
        **overrides,
    }


def test_accept_path_fixtures_are_not_themselves_weak() -> None:
    """The accept-path passwords must not be values the guards are meant to refuse.

    Without this, adding a literal to ``CANONICAL_WEAK_VALUES`` that happens to
    collide with a fixture would turn every accept-path test red for a reason
    that has nothing to do with the guards.
    """
    assert {STRONG_PASSWORD, STRONG_ADMIN_PASSWORD, SERVICE_USERNAME}.isdisjoint(CANONICAL_WEAK_VALUES)
    assert STRONG_PASSWORD != STRONG_ADMIN_PASSWORD


#
# xnat-db — guard-xnat-db-passwords.sh
#
# Every case installs the docker-entrypoint.sh stub, so a guard that stops
# refusing exits 0 instead of 127 and fails the assertion for the right reason.
#


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
@pytest.mark.parametrize("variable", ["POSTGRES_PASSWORD", "XNAT_DATASOURCE_PASSWORD"])
def test_xnat_db_guard_refuses_unset_password(shell: tuple[str, ...], variable: str, stub_bin: Path) -> None:
    """xnat-db must refuse to start when either password is unset."""
    _install_argv_recorder(stub_bin, "docker-entrypoint.sh", stub_bin / "argv")
    env = _xnat_db_env()
    del env[variable]

    result = _run(GUARD_XNAT_DB, env, shell=shell, stub_bin=stub_bin, argv=("postgres",))

    assert result.returncode == 1
    assert f"ERROR: {variable} is not set." in result.stderr
    assert "make generate-xnat-credentials" in result.stderr


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
@pytest.mark.parametrize("variable", ["POSTGRES_PASSWORD", "XNAT_DATASOURCE_PASSWORD"])
@pytest.mark.parametrize("weak", sorted(CANONICAL_WEAK_VALUES))
def test_xnat_db_guard_refuses_weak_password(
    shell: tuple[str, ...], variable: str, weak: str, stub_bin: Path
) -> None:
    """xnat-db must refuse every canonical weak value, on either password."""
    _install_argv_recorder(stub_bin, "docker-entrypoint.sh", stub_bin / "argv")

    result = _run(
        GUARD_XNAT_DB, _xnat_db_env(**{variable: weak}), shell=shell, stub_bin=stub_bin, argv=("postgres",)
    )

    assert result.returncode == 1
    assert f"ERROR: {variable} is set to a weak default or placeholder value." in result.stderr


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
def test_xnat_db_guard_refuses_password_equal_to_username(shell: tuple[str, ...], stub_bin: Path) -> None:
    """xnat-db must refuse XNAT_DATASOURCE_PASSWORD == XNAT_DATASOURCE_USERNAME.

    Exercised with a username outside the weak list — with the shipped ``xnat``
    username the weak arm fires first, so it could not distinguish the two
    branches (the script's ``${XNAT_DATASOURCE_USERNAME:-xnat}`` default is
    belt-and-braces for exactly that reason).
    """
    _install_argv_recorder(stub_bin, "docker-entrypoint.sh", stub_bin / "argv")

    result = _run(
        GUARD_XNAT_DB,
        _xnat_db_env(XNAT_DATASOURCE_PASSWORD=SERVICE_USERNAME),
        shell=shell,
        stub_bin=stub_bin,
        argv=("postgres",),
    )

    assert result.returncode == 1
    assert "ERROR: XNAT_DATASOURCE_PASSWORD is identical to XNAT_DATASOURCE_USERNAME." in result.stderr


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
def test_xnat_db_guard_refuses_reused_superuser_password(shell: tuple[str, ...], stub_bin: Path) -> None:
    """xnat-db must refuse POSTGRES_PASSWORD == XNAT_DATASOURCE_PASSWORD.

    Both values are individually strong here — this is the K8s single-secret
    collapse, where two chart keys point at one entry and the app role silently
    becomes superuser-equivalent.
    """
    _install_argv_recorder(stub_bin, "docker-entrypoint.sh", stub_bin / "argv")

    result = _run(
        GUARD_XNAT_DB,
        _xnat_db_env(POSTGRES_PASSWORD=STRONG_PASSWORD),
        shell=shell,
        stub_bin=stub_bin,
        argv=("postgres",),
    )

    assert result.returncode == 1
    assert "the superuser and xnat role must differ" in result.stderr


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
def test_xnat_db_guard_execs_entrypoint_with_argv_intact(
    shell: tuple[str, ...], stub_bin: Path, tmp_path: Path
) -> None:
    """A strong, distinct pair must reach the stock entrypoint with argv unharmed.

    The reject cases cannot see a ``case`` block that drifts below the ``exec``,
    nor ``exec "$@"`` regressing to ``exec $cmd``; both surface here, the latter
    as a different argument count (``one two`` re-split on ``$IFS``, ``*`` glob
    expanded against the working directory).
    """
    record = tmp_path / "argv"
    _install_argv_recorder(stub_bin, "docker-entrypoint.sh", record)

    result = _run(
        GUARD_XNAT_DB, _xnat_db_env(), shell=shell, stub_bin=stub_bin, argv=("postgres", "one two", "*")
    )

    assert result.returncode == 0, result.stderr
    assert record.read_text().splitlines() == ["3", "postgres", "one two", "*"]


#
# xnat-web — wait-for-postgres.sh
#
# The psql stub is installed on every case, so "psql was never called" is a real
# observation rather than an artefact of the command being absent.
#

XNAT_WEB_REJECTED = [
    *((weak, "xnat") for weak in sorted(CANONICAL_WEAK_VALUES)),
    (SERVICE_USERNAME, SERVICE_USERNAME),
]
XNAT_WEB_REJECTED_IDS = [*sorted(CANONICAL_WEAK_VALUES), "password-equals-username"]


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
@pytest.mark.parametrize("variable", ["XNAT_DATASOURCE_USERNAME", "XNAT_DATASOURCE_PASSWORD"])
def test_xnat_web_entrypoint_refuses_unset_credentials(
    shell: tuple[str, ...], variable: str, stub_bin: Path, tmp_path: Path
) -> None:
    """xnat-web must refuse to start without both datasource credentials."""
    calls = tmp_path / "psql-calls"
    _install_psql_stub(stub_bin, calls)
    _install_argv_recorder(stub_bin, "record-argv", tmp_path / "argv")
    env = _xnat_web_env()
    del env[variable]

    result = _run(WAIT_FOR_POSTGRES, env, shell=shell, stub_bin=stub_bin, argv=("record-argv",))

    assert result.returncode == 1
    assert "XNAT_DATASOURCE_USERNAME and XNAT_DATASOURCE_PASSWORD must be set." in result.stderr
    assert not calls.exists(), "the guard contacted the database before validating credentials"


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
@pytest.mark.parametrize(("password", "username"), XNAT_WEB_REJECTED, ids=XNAT_WEB_REJECTED_IDS)
def test_xnat_web_entrypoint_refuses_weak_password_before_contacting_postgres(
    shell: tuple[str, ...], password: str, username: str, stub_bin: Path, tmp_path: Path
) -> None:
    """xnat-web must refuse every canonical weak value, and password == username.

    The ``psql`` stub would succeed if it were reached, so the absence of its
    call log is the assertion that the guard runs *before* any database contact —
    the ordering a reject-only exit-code check cannot see.
    """
    calls = tmp_path / "psql-calls"
    _install_psql_stub(stub_bin, calls)
    _install_argv_recorder(stub_bin, "record-argv", tmp_path / "argv")

    result = _run(
        WAIT_FOR_POSTGRES,
        _xnat_web_env(XNAT_DATASOURCE_USERNAME=username, XNAT_DATASOURCE_PASSWORD=password),
        shell=shell,
        stub_bin=stub_bin,
        argv=("record-argv",),
    )

    assert result.returncode == 1
    assert "ERROR: XNAT_DATASOURCE_PASSWORD is set to a weak default or placeholder value." in result.stderr
    assert not calls.exists(), "the guard contacted the database before validating credentials"


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
def test_xnat_web_entrypoint_execs_command_with_argv_intact(
    shell: tuple[str, ...], stub_bin: Path, tmp_path: Path
) -> None:
    """A strong password plus a reachable database must exec the original command.

    ``one two`` and ``*`` are the two arguments the pre-fix ``exec $cmd`` mangled.
    """
    record = tmp_path / "argv"
    _install_psql_stub(stub_bin, tmp_path / "psql-calls")
    _install_argv_recorder(stub_bin, "record-argv", record)

    result = _run(
        WAIT_FOR_POSTGRES,
        _xnat_web_env(),
        shell=shell,
        stub_bin=stub_bin,
        argv=("record-argv", "one two", "*"),
    )

    assert result.returncode == 0, result.stderr
    assert record.read_text().splitlines() == ["2", "one two", "*"]


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
@pytest.mark.parametrize(
    "message",
    [
        'psql: error: connection failed: FATAL:  password authentication failed for user "xnat"',
        'psql: error: connection failed: FATAL:  role "xnat" does not exist',
        'psql: error: connection failed: FATAL:  database "xnat" does not exist',
    ],
    ids=["bad-password", "missing-role", "missing-database"],
)
def test_xnat_web_entrypoint_fails_fast_when_postgres_rejects_credentials(
    shell: tuple[str, ...], message: str, stub_bin: Path, tmp_path: Path
) -> None:
    """A reachable database that rejects us must exit, not retry forever.

    This is the rotation case: ``XNAT_DATASOURCE_*`` is applied only at xnat-db's
    first initdb, so a password rotated afterwards mismatches what the database
    holds. Retrying makes that indistinguishable from a slow start-up, which is
    why the remedy text has to be printed and the loop has to end.
    """
    calls = tmp_path / "psql-calls"
    _install_psql_stub(stub_bin, calls, message=message, fail_times=-1)
    _install_argv_recorder(stub_bin, "record-argv", tmp_path / "argv")

    result = _run(WAIT_FOR_POSTGRES, _xnat_web_env(), shell=shell, stub_bin=stub_bin, argv=("record-argv",))

    assert result.returncode == 1
    assert "Postgres is up but rejected the XNAT datasource credentials" in result.stderr
    assert "ALTER ROLE" in result.stderr
    assert calls.read_text().splitlines() == ["call"], "a rejected credential must not be retried"


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
def test_xnat_web_entrypoint_retries_while_postgres_is_unreachable(
    shell: tuple[str, ...], stub_bin: Path, tmp_path: Path
) -> None:
    """A database that is merely not up yet must be waited for, not rejected.

    The other half of the split above: collapsing the two classifications in
    either direction is a regression — reject-as-retry hangs the container,
    retry-as-reject breaks every cold start where xnat-db lags xnat-web.
    """
    calls = tmp_path / "psql-calls"
    record = tmp_path / "argv"
    _install_psql_stub(
        stub_bin,
        calls,
        message='psql: error: connection to server at "xnat-db" failed: Connection refused',
        fail_times=2,
    )
    _install_argv_recorder(stub_bin, "record-argv", record)

    result = _run(WAIT_FOR_POSTGRES, _xnat_web_env(), shell=shell, stub_bin=stub_bin, argv=("record-argv",))

    assert result.returncode == 0, result.stderr
    assert len(calls.read_text().splitlines()) == 3, "expected two retries then a successful connection"
    assert "Postgres is unavailable - sleeping" in result.stderr
    assert record.read_text().splitlines() == ["0"]
