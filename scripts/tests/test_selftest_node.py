#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
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
"""Black-box tests for scripts/selftest_node.sh with make/curl/docker stubbed (FLIP#1390).

Usage:
    uv run --no-config scripts/tests/test_selftest_node.py
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
SCRIPT = SCRIPTS_DIR / "selftest_node.sh"

PASS = 0
FAIL = 0


def _assert(condition: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        print(f"  ✅ {label}")
        PASS += 1
    else:
        print(f"  ❌ {label}")
        for line in detail.splitlines():
            print(f"    {line}")
        FAIL += 1


def _stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text("#!/bin/bash\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _fake_dev_kit(root: Path, backend: str) -> Path:
    """A provisioned dev kit tree as `make -C fl-services/<backend> provision` leaves it."""
    if backend == "nvflare":
        slot = root / "net-1" / "services" / "Trust_1"
        for sub in ("local", "startup", "transfer"):
            (slot / sub).mkdir(parents=True)
        (slot / "startup" / "fed_client.json").write_text("{}")
    else:
        (root / "net-1" / "certificates").mkdir(parents=True)
        (root / "net-1" / "keys").mkdir(parents=True)
        (root / "net-1" / "certificates" / "ca.crt").write_text("ca")
        (root / "net-1" / "keys" / "supernode_credentials_1").write_text("key")
    return root


def _run(
    case: str,
    *,
    backend: str = "nvflare",
    failing_port: str = "",
    write_markers: bool = True,
    fl_states: tuple[str, str] = ("true false 0", "true false 0"),
    store_ok: bool = True,
    wc_reply: str = "",
    stale_kit: bool = False,
    bad_template: bool = False,
    log_lag: int = 0,
) -> tuple[subprocess.CompletedProcess, dict | None, Path, Path]:
    tmp = Path(tempfile.mkdtemp(prefix=f"selftest-{case}-"))
    bin_dir, out, data = tmp / "bin", tmp / "out", tmp / "data"
    bin_dir.mkdir()
    calls = tmp / "calls.log"
    marker_body = "projects=cxr_project\\nsource_trust=1\\nversion=x\\n"
    markers = (
        f'mkdir -p "{data}/omop"; printf "{marker_body}" > "{data}/omop/.seeded"; '
        f'printf "{marker_body}" > "{data}/.orthanc-storage.seeded"'
        if write_markers
        else ":"
    )
    _stub(bin_dir, "make", f'echo "make $*" >> "{calls}"; case "$*" in *up-trust*) {markers};; esac; exit 0')
    for tool in ("chown", "chgrp"):
        _stub(bin_dir, tool, f'echo "{tool} $*" >> "{calls}"; exit 0')
    _stub(bin_dir, "sleep", "exit 0")
    store_reply = '{"FailedInstancesCount":0}' if store_ok else '{"FailedInstancesCount":1}'
    _stub(
        bin_dir,
        "curl",
        f'echo "curl $*" >> "{calls}"\n'
        f'if [ -n "{failing_port}" ]; then case "$*" in *":{failing_port}/health"*) exit 22;; esac; fi\n'
        'case "$*" in\n'
        '  *"%{http_code}"*) echo 401;;\n'
        '  *"/instances?"*) echo \'["abc123"]\';;\n'
        f"  *\"/modalities/XNAT/store\"*) echo '{store_reply}';;\n"
        '  *) echo \'{"status":"ok"}\';;\n'
        "esac\nexit 0",
    )
    counter = tmp / "wc.count"
    states = tmp / "fl-states"
    states.write_text("\n".join(fl_states) + "\n")
    # What the stubbed XNAT container answers to `wc -l`: dicom.log stays put (XNAT writes it only
    # on an importer problem) while received.log grows, or a fixed reply (to prove container output
    # is never evaluated by the runner).
    wc_arm = (
        f"  *\"wc -l\"*) printf '%s\\n' '{wc_reply}';;\n"
        if wc_reply
        else '  *"wc -l"*dicom.log*) echo 7;;\n'
        f'  *"wc -l"*) r=$(( $(cat "{counter}.reads" 2>/dev/null || echo 0) + 1 )); echo $r > "{counter}.reads"; '
        f'n=$(cat "{counter}" 2>/dev/null || echo 10); echo $n; [ $r -gt {log_lag} ] && echo $((n + 5)) > "{counter}";;\n'
    )
    # `docker inspect` answers the FL client's state, one line per call (running restarting count).
    _stub(
        bin_dir,
        "docker",
        f'echo "docker $*" >> "{calls}"\n'
        'if [ "$1" = ps ]; then echo trust1_xnat-web.1; echo trust1-fl-client-net-1-1; fi\n'
        f'if [ "$1" = inspect ]; then head -1 "{states}"; '
        f'tail -n +2 "{states}" > "{states}.n"; mv "{states}.n" "{states}"; fi\n'
        'if [ "$1" = logs ]; then echo "fl-client log line"; fi\n'
        'if [ "$1" = exec ]; then case "$*" in\n'
        f"{wc_arm}"
        '  *tail*) echo "INFO stored instance";;\n'
        "esac; fi\nexit 0",
    )
    kit_out = tmp / ".env.SELFTEST.production"
    if stale_kit:
        kit_out.write_text("STALE_KIT=1\n")
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "SELFTEST_OUT": str(out),
        "SELFTEST_DATA_ROOT": str(data),
        "SELFTEST_SKIP_PROVISION": "1",
        "SELFTEST_KIT_OUT": str(kit_out),
        "SELFTEST_DEV_KIT_DIR": str(_fake_dev_kit(tmp / "devkit", backend)),
        "SELFTEST_FL_KIT_DIR": str(tmp / "fl-kit"),
        "SELFTEST_FL_SETTLE_SECONDS": "0",
    }
    if bad_template:
        template = tmp / "template"
        template.write_text("SOMETHING_NEW=<run-make-register-trust>\n")
        env["SELFTEST_KIT_TEMPLATE"] = str(template)
    result = subprocess.run(["bash", str(SCRIPT), backend], env=env, capture_output=True, text=True, timeout=120)
    reports = sorted(out.glob(f"selftest-{backend}-*.json")) if out.exists() else []
    report = json.loads(reports[-1].read_text()) if reports else None
    return result, report, calls, tmp


def _check(report: dict | None, name: str) -> dict:
    for check in (report or {}).get("checks", []):
        if check["name"] == name:
            return check
    return {}


def main() -> int:
    print("all_checks_pass")
    result, report, calls, tmp = _run("ok")
    _assert(result.returncode == 0, "exits 0", result.stdout + result.stderr)
    _assert(report is not None and report["ok"] is True, "report ok=true")
    for name in (
        "stack up",
        "trust-api health",
        "imaging-api health",
        "data-access-api health",
        "omop seeded",
        "orthanc seeded",
        "orthanc requires auth",
        "xnat c-store",
        "fl client running",
    ):
        _assert(_check(report, name).get("ok") is True, f"check '{name}' passed", json.dumps(_check(report, name)))
    _assert(calls.exists() and "down-trust KIT=SELFTEST PROD=true" in calls.read_text(), "stack torn down afterwards")

    print("fl_kit_is_staged_for_the_nvflare_client")
    staged = tmp / "fl-kit" / "net-1" / "services" / "Trust_1" / "startup" / "fed_client.json"
    _assert(staged.exists(), "the slot is copied out of the root-owned dev kit")
    _assert(
        f"chown -R 1000:1000 {tmp / 'fl-kit' / 'net-1'}" in calls.read_text(), "and handed to the client's uid 1000"
    )
    kit = (tmp / ".env.SELFTEST.production").read_text()
    _assert(f"FL_KIT_DIR={tmp / 'fl-kit'}" in kit, "the kit points the stack at the staged copy")

    print("fl_kit_is_staged_for_the_flower_supernode")
    result, report, calls, tmp = _run("flower", backend="flower")
    key = tmp / "fl-kit" / "net-1" / "keys" / "supernode_credentials_1"
    ca = tmp / "fl-kit" / "net-1" / "certificates" / "ca.crt"
    _assert(key.exists() and ca.exists(), "key and CA certificate staged")
    _assert(key.exists() and (key.stat().st_mode & 0o777) == 0o640, "the private key is 0640")
    _assert(ca.exists() and (ca.stat().st_mode & 0o777) == 0o644, "the CA certificate is 0644")
    _assert(f"chgrp 49999 {key}" in calls.read_text(), "the key's group is the supernode's gid 49999")

    print("crash_looping_fl_client_fails_run")
    result, report, _, _ = _run("loop", fl_states=("true false 3", "true false 4"))
    check = _check(report, "fl client running")
    _assert(result.returncode != 0 and check.get("ok") is False, "a restart between samples is a failure")
    _assert("fl-client log line" in check.get("detail", ""), "the failure carries the client's last log lines")

    print("failed_kit_step_never_starts_a_stale_kit")
    result, report, calls, tmp = _run("stale", stale_kit=True, bad_template=True)
    _assert(not (tmp / ".env.SELFTEST.production").exists(), "the previous run's kit is removed")
    _assert("up-trust" not in calls.read_text(), "up-trust is not run")
    _assert(
        _check(report, "stack up").get("ok") is False and "skipped" in _check(report, "stack up").get("detail", ""),
        "stack up is recorded as skipped",
    )

    print("xnat_imports_after_the_store_returns")
    # XNAT records the receipt asynchronously: the store has returned but the line lands a few reads later.
    result, report, _, _ = _run("lag", log_lag=3)
    _assert(_check(report, "xnat c-store").get("ok") is True, "a log that grows late still passes",
            json.dumps(_check(report, "xnat c-store")))

    print("a_store_xnat_never_received_fails_run")
    result, report, _, _ = _run("unreceived", log_lag=1000)
    check = _check(report, "xnat c-store")
    _assert(check.get("ok") is False and "received.log" in check.get("detail", ""), "names the missing receipt")

    print("health_failure_fails_run")
    result, report, _, _ = _run("health", failing_port="8010")
    _assert(result.returncode != 0, "exits non-zero")
    _assert(report is not None and report["ok"] is False, "report ok=false")
    _assert(_check(report, "data-access-api health").get("ok") is False, "names the failing service")

    print("missing_seed_marker_fails_run")
    result, report, _, _ = _run("seed", write_markers=False)
    _assert(result.returncode != 0 and _check(report, "omop seeded").get("ok") is False, "unseeded OMOP is a failure")

    print("stopped_fl_client_fails_run")
    result, report, _, _ = _run("fl", fl_states=("false false 3", "false false 3"))
    _assert(
        result.returncode != 0 and _check(report, "fl client running").get("ok") is False, "no FL client is a failure"
    )

    print("failed_cstore_fails_run")
    result, report, _, _ = _run("cstore", store_ok=False)
    _assert(
        result.returncode != 0 and _check(report, "xnat c-store").get("ok") is False, "a failed C-STORE is a failure"
    )

    print("container_output_is_never_evaluated")
    canary = Path(tempfile.mkdtemp()) / "PWNED"
    result, report, _, _ = _run("inject", wc_reply=f"a[$(touch {canary})]")
    _assert(not canary.exists(), "a crafted line count from the XNAT container runs nothing")
    _assert(_check(report, "xnat c-store").get("ok") is False, "and the C-STORE check fails instead")

    print("rejects_unknown_backend")
    bad = subprocess.run(["bash", str(SCRIPT), "pytorch"], capture_output=True, text=True)
    _assert(bad.returncode == 2, "exit 2 on an unknown backend")

    print(f"\n==== {PASS} passed, {FAIL} failed ====")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
