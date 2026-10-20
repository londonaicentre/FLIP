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

"""Guards over `make sync-kit`, driven by a fake ``snow`` binary.

The property under test is the one that cannot be checked by reading the code once: a
secret value must never appear in a command line or in anything printed. The fake
records its own ``argv`` and its stdin separately, so the two can be asserted apart -
values are expected on stdin and forbidden everywhere else.

An argument is not private. It is visible in ``ps`` to every user on the host and is
recorded by shell history and by most CI runners' command logging, so a credential
passed as one has leaked before the process has done anything.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import CODE, ENV, SENTINELS

SNOWFLAKE_DIR = Path(__file__).resolve().parents[1]
if str(SNOWFLAKE_DIR) not in sys.path:
    sys.path.insert(0, str(SNOWFLAKE_DIR))

import sync_snowflake_kit as sync  # noqa: E402

FAKE_SNOW = """#!/usr/bin/env python3
import json, sys, os
record = {"argv": sys.argv[1:], "stdin": ""}
if not sys.stdin.isatty():
    try:
        record["stdin"] = sys.stdin.read()
    except Exception:
        record["stdin"] = ""
path = os.environ["FAKE_SNOW_LOG"]
with open(path, "a") as f:
    f.write(json.dumps(record) + "\\n")
print("ok")
"""


@pytest.fixture
def fake_snow(tmp_path: Path) -> tuple[Path, Path]:
    binary = tmp_path / "snow"
    binary.write_text(FAKE_SNOW)
    binary.chmod(0o755)
    return binary, tmp_path / "snow.log"


def _fl_kit(tmp_path: Path) -> Path:
    kit_dir = tmp_path / "fl-kit"
    (kit_dir / "net-1" / "startup").mkdir(parents=True)
    (kit_dir / "net-1" / "startup" / "fed_client.json").write_text("{}\n")
    return kit_dir


def run_sync(fake_snow, kit_file: Path, tmp_path: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    binary, log = fake_snow
    return subprocess.run(
        [
            sys.executable,
            str(SNOWFLAKE_DIR / "sync_snowflake_kit.py"),
            "--kit",
            CODE,
            "--env",
            ENV,
            "--kit-file",
            str(kit_file),
            "--snow",
            str(binary),
            "--fl-kit-dir",
            str(_fl_kit(tmp_path)),
            *extra,
        ],
        text=True,
        capture_output=True,
        env={"PATH": "/usr/bin:/bin", "FAKE_SNOW_LOG": str(log), "HOME": str(tmp_path)},
    )


def _records(log: Path) -> list[dict]:
    return [json.loads(line) for line in log.read_text().splitlines()]


def test_secret_values_never_reach_a_command_line(fake_snow, kit_file: Path, tmp_path: Path) -> None:
    result = run_sync(fake_snow, kit_file, tmp_path)
    assert result.returncode == 0, result.stderr
    for record in _records(fake_snow[1]):
        joined = " ".join(record["argv"])
        for name, value in SENTINELS.items():
            assert value not in joined, f"{name} leaked into argv"


def test_secret_values_never_reach_stdout_or_stderr(fake_snow, kit_file: Path, tmp_path: Path) -> None:
    result = run_sync(fake_snow, kit_file, tmp_path)
    for name, value in SENTINELS.items():
        assert value not in result.stdout, f"{name} leaked into stdout"
        assert value not in result.stderr, f"{name} leaked into stderr"


def test_values_do_reach_snowflake_on_stdin(fake_snow, kit_file: Path, tmp_path: Path) -> None:
    """The counterpart guard: a sync that leaks nothing because it SENT nothing is not
    a pass. Exactly one invocation carries the SQL, on stdin."""
    run_sync(fake_snow, kit_file, tmp_path)
    sql = [record for record in _records(fake_snow[1]) if record["argv"][:1] == ["sql"]]
    assert len(sql) == 1
    assert "--stdin" in sql[0]["argv"]
    for value in SENTINELS.values():
        assert value in sql[0]["stdin"]


def test_dry_run_prints_redacted_sql_and_runs_nothing(fake_snow, kit_file: Path, tmp_path: Path) -> None:
    result = run_sync(fake_snow, kit_file, tmp_path, "--dry-run")
    assert result.returncode == 0, result.stderr
    assert not fake_snow[1].exists(), "a dry run must not invoke snow at all"
    assert "CREATE SECRET IF NOT EXISTS" in result.stdout
    assert sync.REDACTED in result.stdout
    for value in SENTINELS.values():
        assert value not in result.stdout


def test_secrets_are_altered_not_replaced(kit: dict) -> None:
    """CREATE OR REPLACE would drop every grant on the secret, and the next deploy's
    services would fail to start on a privilege error about an object that plainly
    exists. The IF NOT EXISTS + ALTER pair is idempotent and keeps the grants."""
    statements = sync.secret_statements(sync.Names(CODE), kit)
    assert sync.absent_kit_entries(kit) == []
    joined = "\n".join(statements)
    assert "CREATE OR REPLACE SECRET" not in joined
    assert joined.count("CREATE SECRET IF NOT EXISTS") == len(SENTINELS)
    assert joined.count("ALTER SECRET") == len(SENTINELS)
    assert joined.count("GRANT READ ON SECRET") == len(SENTINELS)
    assert "GRANT WRITE" not in joined


def test_secret_objects_follow_the_naming_contract(kit: dict) -> None:
    statements = sync.secret_statements(sync.Names(CODE), kit)
    for secret in sync.SECRETS:
        assert f"FLIP_TRUST_{CODE}.APP.{secret}" in "\n".join(statements)


def test_a_kit_without_a_value_leaves_that_secret_alone(kit: dict) -> None:
    """A trust running no XNAT has no XNAT passwords, and nothing maps them."""
    partial = dict(kit)
    partial["ORTHANC_PASSWORD"] = ""
    statements = sync.secret_statements(sync.Names(CODE), partial)
    assert sync.absent_kit_entries(partial) == ["ORTHANC_PASSWORD"]
    assert "ORTHANC_PASSWORD" not in "\n".join(statements)


def test_quote_in_a_value_cannot_break_out_of_the_literal(kit: dict) -> None:
    """A value is data, never SQL. Backslash is escaped too: Snowflake honours backslash
    escapes inside single-quoted literals, so a trailing one would escape the closing
    quote and turn the rest of the statement into string data."""
    nasty = dict(kit, TRUST_API_KEY="a'; DROP DATABASE FLIP_TRUST_GSTT; --\\")
    statements = sync.secret_statements(sync.Names(CODE), nasty)
    joined = "\n".join(statements)
    assert "DROP DATABASE" in joined  # present as data ...
    # ... inside one quoted literal, so it is never a statement of its own: the quote is
    # escaped, the backslash is escaped, and no statement begins with the injected text.
    assert "\\'; DROP DATABASE" in joined
    assert not any(statement.lstrip().upper().startswith("DROP") for statement in statements)


def test_redacted_statements_never_hold_a_value(kit: dict) -> None:
    """The dry-run form is built from :data:`REDACTED`, not substituted afterwards, so no
    spelling of a value (escaped or not) can survive into what is printed."""
    nasty = dict(kit, AES_KEY_BASE64="we'ird\\value")
    present = [e for e in sync.SECRETS if e not in sync.absent_kit_entries(nasty)]
    redacted = "\n".join(sync.redacted_statements(sync.Names(CODE), present))
    for value in (nasty[var] for var in sync.SECRET_KIT_VAR.values()):
        assert value not in redacted
    assert "we\\'ird" not in redacted
    assert redacted.count(sync.REDACTED) == len(sync.SECRETS)


def test_a_failed_snow_call_does_not_echo_its_output(tmp_path: Path, kit: dict) -> None:
    """``snow`` can quote the failing statement, and the statement carries the value."""
    leaky = tmp_path / "snow"
    leaky.write_text("#!/bin/sh\ncat >&2\nexit 3\n")
    leaky.chmod(0o755)
    statements = sync.secret_statements(sync.Names(CODE), kit)
    try:
        sync.run_sql(str(leaky), "flip-test", statements)
    except sync.SyncError as error:
        message = str(error)
    else:
        raise AssertionError("a failing snow call must raise")
    assert "exit 3" in message
    for var in sync.SECRET_KIT_VAR.values():
        assert kit[var] not in message


def test_fl_kit_is_pushed_under_its_net_prefix(fake_snow, kit_file: Path, tmp_path: Path) -> None:
    """Material at the stage root would be visible to every net's client; the mount is
    ``@...FL_KIT/net-<N>``, so the push has to match it."""
    run_sync(fake_snow, kit_file, tmp_path)
    copies = [record for record in _records(fake_snow[1]) if record["argv"][:2] == ["stage", "copy"]]
    targets = [record["argv"][3] for record in copies]
    assert f"@FLIP_TRUST_{CODE}.STAGES.FL_KIT/net-1/" in targets
    assert all("--recursive" in record["argv"] for record in copies if record["argv"][3].endswith("net-1/"))


def test_a_missing_fl_net_directory_is_refused(fake_snow, kit_file: Path, tmp_path: Path) -> None:
    binary, log = fake_snow
    result = subprocess.run(
        [
            sys.executable,
            str(SNOWFLAKE_DIR / "sync_snowflake_kit.py"),
            "--kit",
            CODE,
            "--env",
            ENV,
            "--kit-file",
            str(kit_file),
            "--snow",
            str(binary),
            "--fl-kit-dir",
            str(tmp_path / "nowhere"),
        ],
        text=True,
        capture_output=True,
        env={"PATH": "/usr/bin:/bin", "FAKE_SNOW_LOG": str(log), "HOME": str(tmp_path)},
    )
    assert result.returncode == 1
    assert "does not exist" in result.stdout


def test_a_placeholder_kit_is_refused_before_anything_runs(fake_snow, tmp_path: Path) -> None:
    unregistered = tmp_path / ".env.GSTT.stag"
    unregistered.write_text("TRUST_API_KEY=<run-make-register-trust>\nAES_KEY_BASE64=\n")
    result = run_sync(fake_snow, unregistered, tmp_path)
    assert result.returncode == 1
    assert not fake_snow[1].exists()
