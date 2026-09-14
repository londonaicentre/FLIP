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
# sim-tutorial.sh clears the flwr SuperLink / Ray workers a previous simulator run left behind,
# because Ray workers inherit the SuperLink's environment and a stale one silently ignores the
# DEV_* / WORKING_DIR exports of the next run. The obvious `pkill -f flower-superlink` is wrong
# on a developer host: the FLIP dev stack's fl-server SuperLink runs in a container whose
# processes are visible in the host PID namespace and match the same pattern. The guard must
# therefore kill only processes that are (a) in this host's PID namespace and (b) started from
# this checkout, and it must decide (a) without a runtime-specific cgroup string — under docker's
# systemd driver /proc/<pid>/cgroup reads docker-<id>.scope, under cgroupfs it is /docker/<id>,
# under kubelet /kubepods/... — so a literal match protects only one setup.
#
# Exercised for real: the function is lifted out of the script and run against decoy processes
# whose command line carries a marker that exists nowhere else (so nothing outside this test can
# match), with the decoys spread across the three cases the guard has to tell apart.

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import time
import uuid
from pathlib import Path

SIM_TUTORIAL = Path(__file__).resolve().parents[1] / "flower" / "sim-tutorial.sh"
LIVE_PATTERN = "flwr-simulation|flwr-serverapp|flower-superlink"
_FUNCTION = re.compile(r"^stop_stale_superlinks\(\) \{\n.*?^\}\n", re.M | re.S)


def _guard_script(marker: str) -> str:
    """The stop_stale_superlinks function from the script, retargeted at ``marker``."""
    match = _FUNCTION.search(SIM_TUTORIAL.read_text())
    assert match, "stop_stale_superlinks() not found in sim-tutorial.sh"
    function = match.group(0)
    assert f'pgrep -f "{LIVE_PATTERN}"' in function, "the guard's pgrep pattern moved; update LIVE_PATTERN"
    return function.replace(LIVE_PATTERN, marker) + "stop_stale_superlinks\n"


def _decoy(marker: str, checkout: str) -> subprocess.Popen[bytes]:
    # The marker reaches the decoy through its own argv only — never through a wrapper shell's,
    # which is the pgrep self-match trap the guard's own caller would otherwise fall into.
    return subprocess.Popen(
        ["sh", "-c", f"sleep 120; echo {marker} {checkout}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )


def _decoy_in_other_pid_namespace(marker: str, checkout: str) -> subprocess.Popen[bytes] | None:
    """A decoy in a fresh PID namespace, standing in for a container's process; None if unavailable."""
    if shutil.which("unshare") is None:
        return None
    probe = subprocess.run(["unshare", "-Urpf", "true"], capture_output=True)
    if probe.returncode != 0:
        return None
    # The outer sh -c string quotes $MARKER/$CHECKOUT literally, so only the innermost process — the
    # one inside the new namespace — carries the expanded marker in its argv. It is a child of the
    # namespace's init, not init itself, and it restores SIGTERM's default disposition: init in a
    # PID namespace has SIGTERM ignored and a child inherits that, so a guard that does target the
    # process would leave it standing and pass this test for the wrong reason.
    decoy = 'python3 -c "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_DFL); time.sleep(120)"'
    return subprocess.Popen(
        ["unshare", "-Urpf", "--kill-child", "sh", "-c", f"{decoy} $MARKER $CHECKOUT & wait"],
        env={**os.environ, "MARKER": marker, "CHECKOUT": checkout},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _pids_matching(marker: str) -> set[int]:
    out = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True).stdout.split()
    return {int(pid) for pid in out}


def _wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def test_kills_only_this_checkouts_processes_in_this_pid_namespace(tmp_path: Path):
    marker = f"stale-guard-{uuid.uuid4().hex}"
    checkout = str(tmp_path / "checkout")
    own = _decoy(marker, checkout)
    other = _decoy(marker, str(tmp_path / "elsewhere"))
    namespaced = _decoy_in_other_pid_namespace(marker, checkout)
    try:
        expected = 3 if namespaced else 2
        assert _wait_for(lambda: len(_pids_matching(marker)) >= expected), "decoys did not all start"
        before = _pids_matching(marker)
        inner = before - {own.pid, other.pid}
        assert len(inner) == (1 if namespaced else 0), before

        script = tmp_path / "guard.sh"
        script.write_text(_guard_script(marker))
        result = subprocess.run(
            ["bash", str(script)], env={**os.environ, "REPO_ROOT": checkout}, capture_output=True, text=True
        )

        assert result.returncode == 0, result.stderr
        assert own.wait(timeout=5) == -signal.SIGTERM, "the stale process from this checkout was not stopped"
        assert f"stopped stale simulator process {own.pid}" in result.stdout
        assert other.poll() is None, "a process from another checkout was killed"
        if namespaced:
            assert namespaced.poll() is None
            assert inner <= _pids_matching(marker), "a process in another PID namespace (a container's) was killed"
        assert str(other.pid) not in result.stdout
    finally:
        for proc in (own, other, namespaced):
            if proc is not None and proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
        subprocess.run(["pkill", "-9", "-f", marker], capture_output=True)


def test_guard_decides_containment_by_pid_namespace_not_cgroup_string():
    function = _FUNCTION.search(SIM_TUTORIAL.read_text())
    assert function
    assert "/ns/pid" in function.group(0)
    assert 'grep -q "docker-"' not in function.group(0), (
        "the guard fell back to grepping /proc/<pid>/cgroup for a runtime-specific string"
    )
