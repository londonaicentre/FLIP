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
# (FLIP#1249). Two properties of flwr make a raw simulator "pass" meaningless: `flwr run --stream`
# returns 0 once the log stream closes whatever became of the run, so a simulation that dies
# mid-round still hands `make sim-tutorial` a success; and `flwr run . local` connects to whatever
# already listens on the local Control API port, so a SuperLink left behind by ANOTHER checkout
# captures the run and executes the app in that checkout's environment, with nothing in the
# output saying so but the venv paths in a traceback.
#
# The script's functions are sourced (SIM_TUTORIAL_LIB=1 stops it short of the main flow) and
# run against a fake `uv` on PATH whose `flwr ls` answers scripted statuses in flwr 1.36's real
# reply shapes, with a real listener standing in for the SuperLink on a throwaway port. The main
# flow is then run end to end the same way, with `flwr run` faked too. test_sim_tutorial_stale_guard.py
# covers the stale-process cleanup that runs before the foreign-SuperLink check.

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

FLOWER = Path(__file__).resolve().parents[1] / "flower"
SIM_TUTORIAL = FLOWER / "sim-tutorial.sh"

# `uv run --project <flip-utils> --extra full <cmd> …`: `flwr ls` and `flwr run` are faked, `python`
# runs the real interpreter (the script parses the `flwr ls` reply with it). FAKE_STATUSES is one
# entry per `flwr ls` call, comma-separated, the last repeating: a status; empty for flwr's
# "Run ID not found" reply; `!<message>` for any other failure — both of which flwr reports as
# {"success": false, "error-message": …} with exit 0. FAKE_LS_CALLS counts the calls. `flwr run`
# starts a listener on the control port (what the real one does by starting a SuperLink), prints
# the run-id line unless FAKE_RUN_SILENT is set, and exits FAKE_RUN_RC.
FAKE_UV = """#!/usr/bin/env bash
case " $* " in
  *" flwr ls local --run-id "*)
    n=$(cat "$FAKE_LS_CALLS" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$FAKE_LS_CALLS"
    entry="$(printf '%s' "$FAKE_STATUSES" | awk -v n="$n" -F, '{ i = (n > NF) ? NF : n; print $i }')"
    run_id="${*##* --run-id }"; run_id="${run_id%% *}"
    case "$entry" in
      "")  printf '{\\n  "success": false,\\n  "error-message": "[code: 16] Run ID not found."\\n}\\n' ;;
      !*)  printf '{\\n  "success": false,\\n  "error-message": "%s"\\n}\\n' "${entry#!}" ;;
      *)   printf '{\\n  "success": true,\\n  "runs": [\\n    {\\n      "run-id": "%s",\\n' "$run_id"
           printf '      "status": "%s",\\n      "status-details": "N/A"\\n    }\\n  ]\\n}\\n' "$entry" ;;
    esac ;;
  *" flwr run . local "*)
    python3 -c 'import socket, sys, time
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("127.0.0.1", int(sys.argv[1]))); s.listen(); time.sleep(120)' "$FLWR_LOCAL_CONTROL_API_PORT" >/dev/null 2>&1 &
    echo $! > "$FAKE_LISTENER_PID"
    probe='import socket, sys; socket.create_connection(("127.0.0.1", int(sys.argv[1])), timeout=1).close()'
    until python3 -c "$probe" "$FLWR_LOCAL_CONTROL_API_PORT" 2>/dev/null; do sleep 0.05; done
    env > "$FAKE_RUN_ENV"
    echo "Using SuperLink: local (127.0.0.1:$FLWR_LOCAL_CONTROL_API_PORT)"
    [ -n "${FAKE_RUN_SILENT:-}" ] || echo "Successfully started run 4242"
    exit "${FAKE_RUN_RC:-0}" ;;
  *" python "*)
    shift 5; shift; exec python3 "$@" ;;
  *) echo "fake uv: unexpected invocation: $*" >&2; exit 97 ;;
esac
"""


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _listener(port: int = 0) -> tuple[subprocess.Popen[str], int]:
    """A process listening on 127.0.0.1:<port> (0 = any), standing in for a SuperLink."""
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import socket, sys, time\n"
            "s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
            "s.bind(('127.0.0.1', int(sys.argv[1]))); s.listen()\n"
            "print(s.getsockname()[1], flush=True); time.sleep(120)",
            str(port),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout is not None
    return proc, int(proc.stdout.readline())


def _stop(proc: subprocess.Popen[str] | None) -> None:
    if proc is not None and proc.poll() is None:
        proc.kill()
        proc.wait(timeout=5)


@pytest.fixture
def fake_bin(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "uv").write_text(FAKE_UV)
    (bin_dir / "uv").chmod(0o755)
    return bin_dir


@pytest.fixture
def fake_uv(fake_bin: Path, tmp_path: Path) -> Iterator[dict[str, str]]:
    """Env with the fake `uv` first on PATH, no poll sleeps, and a live SuperLink stand-in."""
    superlink, port = _listener()
    try:
        yield {
            **os.environ,
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "FAKE_LS_CALLS": str(tmp_path / "ls-calls"),
            "FLWR_LOCAL_CONTROL_API_PORT": str(port),
            "SIM_STATUS_POLLS": "4",
            "SIM_STATUS_POLL_SECS": "0",
        }
    finally:
        _stop(superlink)


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
    assert "knows no run 7" in result.stdout
    assert _ls_calls(fake_uv) == int(fake_uv["SIM_STATUS_POLLS"])


def test_a_failed_query_stops_polling_and_shows_the_reason(fake_uv: dict[str, str]):
    """flwr reports every failure as success:false with exit 0 — it must not read as 'no such run'."""
    env = {**fake_uv, "FAKE_STATUSES": "!Connection to the SuperLink is unavailable."}
    result = _call("assert_run_completed 7", env)
    assert result.returncode != 0
    assert "Connection to the SuperLink is unavailable" in result.stderr
    assert "could not query run 7" in result.stdout
    assert "knows no run" not in result.stdout
    assert _ls_calls(fake_uv) == 1


def test_the_status_survives_forced_colour(fake_uv: dict[str, str]):
    """rich colours the JSON under FORCE_COLOR even through a pipe; the script must still parse it."""
    env = {**fake_uv, "FAKE_STATUSES": "finished:completed", "FORCE_COLOR": "1"}
    result = _call("assert_run_completed 4242", env)
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_dead_superlink_fails_the_verdict_without_querying(fake_bin: Path, tmp_path: Path):
    """`flwr ls` would quietly start a fresh SuperLink and report the stale status the old one saved."""
    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "FAKE_LS_CALLS": str(tmp_path / "ls-calls"),
        "FLWR_LOCAL_CONTROL_API_PORT": str(_free_port()),
        "FAKE_STATUSES": "running",
        "SIM_STATUS_POLL_SECS": "0",
    }
    result = _call("assert_run_completed 7", env)
    assert result.returncode != 0
    assert "is gone" in result.stdout
    assert _ls_calls(env) == 0


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


def test_a_listener_this_checkout_did_not_start_is_refused():
    listener, port = _listener()
    try:
        env = {**os.environ, "FLWR_LOCAL_CONTROL_API_PORT": str(port)}
        result = _call('refuse_foreign_superlink "$CONTROL_PORT"', env)
        assert result.returncode != 0, result.stdout
        assert f"127.0.0.1:{port} is already served by a local SuperLink this checkout did not start" in result.stdout
        assert "execute inside that SuperLink's environment" in result.stdout
        if shutil.which("ss"):
            assert f"pid {listener.pid}:" in result.stdout, result.stdout
    finally:
        _stop(listener)


def test_a_free_control_port_passes_the_check():
    env = {**os.environ, "FLWR_LOCAL_CONTROL_API_PORT": str(_free_port())}
    result = _call('refuse_foreign_superlink "$CONTROL_PORT"', env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == ""


def test_a_port_probe_that_cannot_tell_counts_as_taken(tmp_path: Path):
    """An `ss` that errors (netlink blocked in a sandbox) must not read as 'port free'."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "ss").write_text(
        "#!/usr/bin/env bash\necho 'Cannot open netlink socket: Operation not permitted' >&2\nexit 255\n"
    )
    (bin_dir / "ss").chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "FLWR_LOCAL_CONTROL_API_PORT": str(_free_port()),
    }
    result = _call('refuse_foreign_superlink "$CONTROL_PORT"', env)
    assert result.returncode != 0
    assert "ss could not probe" in result.stderr
    assert "already served" in result.stdout


def test_the_port_release_wait_returns_once_the_port_frees():
    listener, port = _listener()
    proc = None
    try:
        env = {**os.environ, "SIM_PORT_RELEASE_POLLS": "40"}
        started = time.monotonic()
        # Free the port 300 ms in; the wait must return well inside its 4 s budget.
        proc = subprocess.Popen(
            ["bash", "-c", f"SIM_TUTORIAL_LIB=1; source {SIM_TUTORIAL}; wait_for_port_release {port}"],
            env=env,
            stdout=subprocess.PIPE,
            text=True,
        )
        time.sleep(0.3)
        _stop(listener)
        stdout, _ = proc.communicate(timeout=10)
        assert proc.returncode == 0, stdout
        assert time.monotonic() - started < 3
    finally:
        _stop(listener)
        if proc is not None and proc.poll() is None:
            proc.kill()


def test_the_port_release_wait_gives_up_loudly():
    listener, port = _listener()
    try:
        env = {**os.environ, "SIM_PORT_RELEASE_POLLS": "3"}
        result = _call(f"wait_for_port_release {port}", env)
        assert result.returncode != 0
        assert f"still holds 127.0.0.1:{port}" in result.stdout
    finally:
        _stop(listener)


def test_the_library_flag_only_works_when_sourced():
    """Executed with SIM_TUTORIAL_LIB=1 the script must refuse, not exit 0 having done nothing."""
    result = subprocess.run(
        ["bash", str(SIM_TUTORIAL), "ehr_risk_prediction"],
        env={**os.environ, "SIM_TUTORIAL_LIB": "1"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "for sourcing" in result.stderr


def test_every_flower_tutorial_has_a_simulator_data_mapping():
    """A tutorial the case statement does not name fails with `No data mapping`."""
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


# ---- the main flow, end to end, with `flwr run` faked too -------------------------------------


@pytest.fixture
def main_flow_env(fake_bin: Path, tmp_path: Path) -> Iterator[dict[str, str]]:
    """Everything the script needs to run the EHR tutorial against the fakes.

    A `pgrep` that finds nothing keeps the stale-process cleanup away from real processes; the
    dataset is a synthetic tree under SIM_DATA_ROOT; the control port is a free one the fake
    `flwr run` will listen on.
    """
    (fake_bin / "pgrep").write_text("#!/usr/bin/env bash\nexit 1\n")
    (fake_bin / "pgrep").chmod(0o755)
    data = tmp_path / "data" / "synthea"
    data.mkdir(parents=True)
    (data / "dataframe.csv").write_text("person_id\n1\n")
    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "FAKE_LS_CALLS": str(tmp_path / "ls-calls"),
        "FAKE_LISTENER_PID": str(tmp_path / "listener.pid"),
        "FAKE_RUN_ENV": str(tmp_path / "run.env"),
        "FLWR_LOCAL_CONTROL_API_PORT": str(_free_port()),
        "SIM_DATA_ROOT": str(tmp_path / "data"),
        "WORKING_DIR": str(tmp_path / "runs"),
        "SIM_STATUS_POLLS": "4",
        "SIM_STATUS_POLL_SECS": "0",
    }
    env.pop("SIM_TUTORIAL_LIB", None)
    try:
        yield env
    finally:
        pid_file = Path(env["FAKE_LISTENER_PID"])
        if pid_file.exists():
            subprocess.run(["kill", pid_file.read_text().strip()], capture_output=True)


def _run_main_flow(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(SIM_TUTORIAL), "ehr_risk_prediction"], env=env, capture_output=True, text=True)


def test_main_flow_passes_a_completed_run(main_flow_env: dict[str, str]):
    result = _run_main_flow({**main_flow_env, "FAKE_STATUSES": "running,finished:completed"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "run 4242 finished:completed" in result.stdout
    dumped = Path(main_flow_env["FAKE_RUN_ENV"]).read_text().splitlines()
    run_env = dict(line.split("=", 1) for line in dumped if "=" in line)
    # The EHR mapping: dataframe only, no images directory, the flags the app relies on.
    assert run_env["DEV_DATAFRAME"].endswith("synthea/dataframe.csv")
    assert "DEV_IMAGES_DIR" not in run_env
    assert run_env["LOCAL_DEV"] == "true"
    assert run_env["PYTHONUNBUFFERED"] == "1"
    assert run_env["WORKING_DIR"] == main_flow_env["WORKING_DIR"]


def test_main_flow_fails_a_failed_run(main_flow_env: dict[str, str]):
    result = _run_main_flow({**main_flow_env, "FAKE_STATUSES": "finished:failed"})
    assert result.returncode != 0
    assert "run 4242 ended finished:failed" in result.stdout


def test_main_flow_propagates_a_failed_submission(main_flow_env: dict[str, str]):
    """`flwr run` itself failing (a refused FAB, no SuperLink) must be the script's exit status."""
    result = _run_main_flow({**main_flow_env, "FAKE_RUN_RC": "3", "FAKE_STATUSES": "finished:completed"})
    assert result.returncode == 3, result.stdout + result.stderr
    assert _ls_calls(main_flow_env) == 0


def test_main_flow_fails_without_a_run_id(main_flow_env: dict[str, str]):
    result = _run_main_flow({**main_flow_env, "FAKE_RUN_SILENT": "1", "FAKE_STATUSES": "finished:completed"})
    assert result.returncode != 0
    assert "printed no run id" in result.stdout
    assert _ls_calls(main_flow_env) == 0
