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
# sim-tutorial.sh's exit status is the RUN's, and the run goes to a SuperLink the script started
# (FLIP#1249). Two things made a simulator "pass" meaningless before: `flwr run --stream` returns
# 0 once the log stream closes whatever became of the run, so a simulation that died mid-round
# still handed `make sim-tutorial` a success; and `flwr run . local` connects to whatever already
# listens on the local Control API port, so a SuperLink left behind by ANOTHER checkout captured
# the run and executed the app in that checkout's environment, with nothing in the output saying
# so but the venv paths in a traceback.
#
# The script's functions are sourced (SIM_TUTORIAL_LIB=1 stops it short of the main flow) and
# run against a fake `uv` on PATH that answers `flwr ls` with scripted statuses, and against a
# real listener on a throwaway port. test_sim_tutorial_stale_guard.py covers the stale-process
# cleanup that runs before the foreign-SuperLink check.

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

FLOWER = Path(__file__).resolve().parents[1] / "flower"
SIM_TUTORIAL = FLOWER / "sim-tutorial.sh"

FAKE_UV = """#!/usr/bin/env bash
# Stands in for `uv run --project <flip-utils> --extra full flwr ls local --run-id <id> --format json`.
# FAKE_STATUSES is a comma-separated status per call (the last one repeats); an empty entry means
# the SuperLink knows no such run. FAKE_LS_CALLS counts the calls.
case " $* " in
  *" flwr ls local --run-id "*)
    n=$(cat "$FAKE_LS_CALLS" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$FAKE_LS_CALLS"
    status="$(printf '%s' "$FAKE_STATUSES" | awk -v n="$n" -F, '{ i = (n > NF) ? NF : n; print $i }')"
    run_id="${*##* --run-id }"; run_id="${run_id%% *}"
    if [ -z "$status" ]; then printf '{\\n  "success": true,\\n  "runs": []\\n}\\n'; exit 0; fi
    printf '{\\n  "success": true,\\n  "runs": [\\n    {\\n      "run-id": "%s",\\n' "$run_id"
    printf '      "status": "%s",\\n      "status-details": "N/A",\\n' "$status"
    printf '      "network-traffic": {\\n        "inbound-bytes": 0\\n      }\\n    }\\n  ]\\n}\\n'
    ;;
  *) echo "fake uv: unexpected invocation: $*" >&2; exit 97 ;;
esac
"""


@pytest.fixture
def fake_uv(tmp_path: Path) -> dict[str, str]:
    """Env that puts the fake `uv` first on PATH and disables the poll sleeps."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "uv").write_text(FAKE_UV)
    (bin_dir / "uv").chmod(0o755)
    return {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "FAKE_LS_CALLS": str(tmp_path / "ls-calls"),
        "SIM_STATUS_POLLS": "4",
        "SIM_STATUS_POLL_SECS": "0",
    }


def _call(function: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", f"SIM_TUTORIAL_LIB=1; source {SIM_TUTORIAL}; {function}"],
        env=env,
        capture_output=True,
        text=True,
    )


def _ls_calls(env: dict[str, str]) -> int:
    path = Path(env["FAKE_LS_CALLS"])
    return int(path.read_text()) if path.exists() else 0


def test_a_completed_run_passes(fake_uv: dict[str, str]):
    result = _call("assert_run_completed 4242", {**fake_uv, "FAKE_STATUSES": "finished:completed"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "run 4242 finished:completed" in result.stdout
    assert _ls_calls(fake_uv) == 1


def test_a_failed_run_fails_the_script(fake_uv: dict[str, str]):
    result = _call("assert_run_completed 4242", {**fake_uv, "FAKE_STATUSES": "finished:failed"})
    assert result.returncode != 0
    assert "run 4242 ended finished:failed" in result.stdout


def test_the_verdict_waits_for_a_terminal_status(fake_uv: dict[str, str]):
    result = _call("assert_run_completed 7", {**fake_uv, "FAKE_STATUSES": "running,running,finished:completed"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert _ls_calls(fake_uv) == 3


def test_a_run_that_never_ends_is_a_failure_not_a_hang(fake_uv: dict[str, str]):
    result = _call("assert_run_completed 7", {**fake_uv, "FAKE_STATUSES": "running"})
    assert result.returncode != 0
    assert "still 'running'" in result.stdout
    assert _ls_calls(fake_uv) == int(fake_uv["SIM_STATUS_POLLS"])


def test_an_unknown_run_is_a_failure(fake_uv: dict[str, str]):
    result = _call("assert_run_completed 7", {**fake_uv, "FAKE_STATUSES": ""})
    assert result.returncode != 0
    assert "reports no such run" in result.stdout


def test_run_id_is_read_off_the_streamed_output(tmp_path: Path):
    stream = tmp_path / "stream"
    stream.write_text(
        "Using SuperLink: local (127.0.0.1:39093)\n"
        "Successfully started run 8231717412201217875\n"
        "INFO :      Starting logstream for run_id `8231717412201217875`\n"
    )
    assert _call(f"run_id_from {stream}", dict(os.environ)).stdout.strip() == "8231717412201217875"
    stream.write_text("Connection to the SuperLink is unavailable.\n")
    assert _call(f"run_id_from {stream}", dict(os.environ)).returncode != 0


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_a_listener_this_checkout_did_not_start_is_refused():
    listener = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import socket, time\n"
            "s = socket.socket(); s.bind(('127.0.0.1', 0)); s.listen()\n"
            "print(s.getsockname()[1], flush=True); time.sleep(120)",
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert listener.stdout is not None
        port = int(listener.stdout.readline())
        env = {**os.environ, "FLWR_LOCAL_CONTROL_API_PORT": str(port)}
        result = _call('refuse_foreign_superlink "$CONTROL_PORT"', env)
        assert result.returncode != 0, result.stdout
        assert f"127.0.0.1:{port} is already served by a local SuperLink this checkout did not start" in result.stdout
        assert "execute inside that SuperLink's environment" in result.stdout
        if shutil.which("ss"):
            assert f"pid {listener.pid}:" in result.stdout, result.stdout
    finally:
        listener.kill()
        listener.wait(timeout=5)


def test_a_free_control_port_passes_the_check():
    env = {**os.environ, "FLWR_LOCAL_CONTROL_API_PORT": str(_free_port())}
    result = _call('refuse_foreign_superlink "$CONTROL_PORT"', env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == ""


def test_every_flower_tutorial_has_a_simulator_data_mapping():
    """A tutorial the case statement does not name fails with `No data mapping` — the EHR one did."""
    text = SIM_TUTORIAL.read_text()
    start = text.index('case "$TUTORIAL" in')
    block = text[start : text.index("esac", start)]
    mapped = {label for line in block.splitlines() for label in _case_labels(line)}
    tutorials = {p.parent.name for p in FLOWER.glob("*/app") if p.is_dir()}
    assert tutorials, "no Flower tutorials found — has the layout changed?"
    assert tutorials <= mapped, f"tutorials without a data mapping in sim-tutorial.sh: {sorted(tutorials - mapped)}"


def _case_labels(line: str) -> list[str]:
    match = re.match(r"\s*([A-Za-z0-9_|]+)\)", line)
    return match.group(1).split("|") if match else []


def test_the_main_flow_asks_the_superlink_for_the_verdict():
    """`exec`ing flwr would hand its exit status straight to make and skip the verdict."""
    text = SIM_TUTORIAL.read_text()
    assert "exec uv run" not in text
    assert 'exec "${FLIP_UV[@]}"' not in text
    assert 'assert_run_completed "$RUN_ID"' in text
    assert text.index("refuse_foreign_superlink \"$CONTROL_PORT\"") > text.index("stop_stale_superlinks\n[")
