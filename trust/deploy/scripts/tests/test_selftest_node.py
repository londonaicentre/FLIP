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
"""Black-box tests for trust/deploy/scripts/selftest_node.sh with make and docker stubbed (FLIP#1390).

Usage:
    uv run --no-config trust/deploy/scripts/tests/test_selftest_node.py
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
sys.path.insert(0, str(SCRIPTS_DIR))

import node_checks  # noqa: E402 — for ROWS, the list every platform reports

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
    stale_kit: bool = False,
    bad_template: bool = False,
    no_kit: bool = False,
    checker: str = "",
    keep_up: bool = False,
    seed: str = "",
    template_line: str = "",
) -> tuple[subprocess.CompletedProcess, dict | None, Path, Path]:
    """Run the self-test with make, chown/chgrp and docker stubbed.

    The checks are check_node.py's and are tested with it (test_check_node.py). Here the real one runs against a
    docker that answers nothing, so its rows fail and the report still carries the preparation rows; ``checker``
    replaces it with a shell body, to see what it is called with and what its exit does.
    """
    tmp = Path(tempfile.mkdtemp(prefix=f"selftest-{case}-"))
    bin_dir, out, data = tmp / "bin", tmp / "out", tmp / "data"
    bin_dir.mkdir()
    calls = tmp / "calls.log"
    _stub(bin_dir, "make", f'echo "make $*" >> "{calls}"; exit 0')
    for tool in ("chown", "chgrp", "docker"):
        _stub(bin_dir, tool, f'echo "{tool} $*" >> "{calls}"; exit 0')
    kit_out = tmp / ".env.SELFTEST.prod"
    if stale_kit:
        kit_out.write_text("STALE_KIT=1\n")
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "SELFTEST_OUT": str(out),
        "SELFTEST_DATA_ROOT": str(data),
        "SELFTEST_SKIP_PROVISION": "1",
        "SELFTEST_KIT_OUT": str(kit_out),
        "SELFTEST_DEV_KIT_DIR": str(tmp / "missing-kit") if no_kit else str(_fake_dev_kit(tmp / "devkit", backend)),
        "SELFTEST_FL_KIT_DIR": str(tmp / "fl-kit"),
        "SELFTEST_FL_SETTLE_SECONDS": "0",
    }
    env.pop("FLIP_INSTANCE", None)
    if checker:
        _stub(bin_dir, "check-node-stub", f'echo "check_node $*" >> "{calls}"\n{checker}')
        env["SELFTEST_CHECK_NODE"] = str(bin_dir / "check-node-stub")
    if keep_up:
        env["SELFTEST_KEEP_UP"] = "1"
    if seed:
        env["SELFTEST_TRUST_DATA_SEED"] = seed
    if bad_template:
        template = tmp / "template"
        template.write_text("SOMETHING_NEW=<run-make-register-trust>\n")
        env["SELFTEST_KIT_TEMPLATE"] = str(template)
    if template_line:
        template = tmp / "template"
        template.write_text((SCRIPT.parents[2] / ".env.example").read_text() + template_line + "\n")
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
    print("preparation_rows_head_the_report_and_the_node_checks_follow")
    result, report, calls, tmp = _run("ok")
    names = [c["name"] for c in (report or {}).get("checks", [])]
    for name in ("fl kit on the node", "fl kit staged", "kit file", "stack up"):
        _assert(_check(report, name).get("ok") is True, f"preparation row '{name}' passed", json.dumps(report))
    _assert(names[4:] == list(node_checks.ROWS), "then every row check_node.py reports, in order", str(names))
    _assert(len(names) == len(set(names)), "no row is named twice", str(names))
    _assert(
        result.returncode == 1 and _check(report, "services ready").get("ok") is False,
        "with no containers the node checks fail the run",
        result.stdout + result.stderr,
    )
    _assert((tmp / "out" / "latest-nvflare.md").is_file(), "latest-<backend>.md is written")
    _assert("down-trust KIT=SELFTEST HUB=prod" in calls.read_text(), "stack torn down afterwards")

    print("check_node_is_called_for_the_selftest_stack_and_its_exit_is_the_runs")
    for code in (0, 2):
        result, _, calls, tmp = _run(f"checker-{code}", checker=f"exit {code}")
        _assert(result.returncode == code, f"check_node's exit {code} is the run's", result.stdout + result.stderr)
    line = next(line for line in calls.read_text().splitlines() if line.startswith("check_node "))
    for arg in (
        "--project trust1",
        "--xnat-stack xnat1",
        "--backend nvflare",
        f"--kit-file {tmp / '.env.SELFTEST.prod'}",
        "--stem selftest-nvflare-",
        "--latest latest-nvflare",
        "--fl-settle-seconds 0",
    ):
        _assert(arg in line, f"called with {arg}", line)
    _assert("--site" not in line, "and never as a site's own stack: the self-test stack is a throwaway")

    print("an_aborted_run_still_tears_the_stack_down")
    aborted, _, aborted_calls, _ = _run("abort", checker='kill -TERM "$PPID"; exit 0')
    _assert(aborted.returncode == 143, "the run exits on the signal", aborted.stdout + aborted.stderr)
    _assert("down-trust KIT=SELFTEST HUB=prod" in aborted_calls.read_text(), "the trap tears the stack down")

    print("keep_up_survives_an_abort")
    _, _, kept_calls, _ = _run("keepup", checker='kill -TERM "$PPID"; exit 0', keep_up=True)
    _assert("down-trust" not in kept_calls.read_text(), "SELFTEST_KEEP_UP=1 leaves the stack up")

    print("fl_kit_is_staged_for_the_nvflare_client")
    result, report, calls, tmp = _run("staged")
    staged = tmp / "fl-kit" / "net-1" / "services" / "Trust_1" / "startup" / "fed_client.json"
    _assert(staged.exists(), "the slot is copied out of the root-owned dev kit")
    _assert(
        f"chown -R 1000:1000 {tmp / 'fl-kit' / 'net-1'}" in calls.read_text(), "and handed to the client's uid 1000"
    )
    kit = (tmp / ".env.SELFTEST.prod").read_text()
    _assert(f"FL_KIT_DIR={tmp / 'fl-kit'}" in kit, "the kit points the stack at the staged copy")

    print("fl_kit_is_staged_for_the_flower_supernode")
    result, report, calls, tmp = _run("flower", backend="flower")
    key = tmp / "fl-kit" / "net-1" / "keys" / "supernode_credentials_1"
    ca = tmp / "fl-kit" / "net-1" / "certificates" / "ca.crt"
    _assert(key.exists() and ca.exists(), "key and CA certificate staged")
    _assert(key.exists() and (key.stat().st_mode & 0o777) == 0o640, "the private key is 0640")
    _assert(ca.exists() and (ca.stat().st_mode & 0o777) == 0o644, "the CA certificate is 0644")
    _assert(f"chgrp 49999 {key}" in calls.read_text(), "the key's group is the supernode's gid 49999")

    print("failed_kit_step_never_starts_a_stale_kit")
    result, report, calls, tmp = _run("stale", stale_kit=True, bad_template=True)
    _assert(not (tmp / ".env.SELFTEST.prod").exists(), "the previous run's kit is removed")
    _assert("up-trust" not in calls.read_text(), "up-trust is not run")
    _assert(
        _check(report, "stack up").get("ok") is False and "skipped" in _check(report, "stack up").get("detail", ""),
        "stack up is recorded as skipped",
    )

    print("a_delivered_kit_is_checked_before_anything_starts")
    result, report, calls, tmp = _run("delivered")
    _assert(
        _check(report, "fl kit on the node").get("ok") is True,
        "a delivered kit is recorded",
        json.dumps(_check(report, "fl kit on the node")),
    )
    result, report, calls, tmp = _run("nokit", no_kit=True)
    check = _check(report, "fl kit on the node")
    _assert(
        check.get("ok") is False and "missing-kit" in check.get("detail", ""), "a missing kit names where it looked"
    )
    _assert("up-trust" not in calls.read_text(), "and the stack is not started")
    _assert("down-trust" not in calls.read_text(), "so there is nothing to tear down")

    print("sample_data_off_reaches_the_kit_check_node_reads")
    _, report, _, tmp = _run("noseed", seed="false")
    _assert("TRUST_DATA_SEED=false" in (tmp / ".env.SELFTEST.prod").read_text(), "the kit turns the seed off")
    detail = _check(report, "omop seeded").get("detail", "")
    _assert(detail.startswith("TRUST_DATA_SEED=false"), "and the seeded rows are skipped, saying why", detail)

    print("sample_data_off_in_the_template_reads_as_make_reads_it")
    for line in ('TRUST_DATA_SEED="false"', "TRUST_DATA_SEED=false  # real trust"):
        _, report, _, _ = _run("spelt", template_line=line)
        row = _check(report, "omop seeded")
        _assert(row.get("status") == "skipped", f"{line!r}: the seeded rows are skipped", json.dumps(row))

    print("rejects_unknown_backend")
    bad = subprocess.run(["bash", str(SCRIPT), "pytorch"], capture_output=True, text=True)
    _assert(bad.returncode == 2, "exit 2 on an unknown backend")

    print(f"\n==== {PASS} passed, {FAIL} failed ====")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
