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
"""Black-box tests for scripts/selftest_kit.py — the hub-less kit for an Azure node's self-test (FLIP#1390).

Usage:
    uv run --no-config scripts/tests/test_selftest_kit.py
"""

from __future__ import annotations

import base64
import re
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = SCRIPTS_DIR.parent
SCRIPT = SCRIPTS_DIR / "selftest_kit.py"
TEMPLATE = REPO_ROOT / "trust" / ".env.example"

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


def _run(out: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--backend",
            "nvflare",
            "--fl-kit-dir",
            "/opt/kit",
            "--data-root",
            "/opt/flip/data/trust-1",
            "--out",
            str(out),
            "--template",
            str(TEMPLATE),
            *extra,
        ],
        capture_output=True,
        text=True,
    )


def _kv(path: Path) -> dict[str, str]:
    pairs: dict[str, str] = {}
    if not path.exists():
        return pairs
    for line in path.read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            pairs[key] = value
    return pairs


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    out = tmp / ".env.SELFTEST.production"

    print("placeholders_all_filled")
    result = _run(out)
    _assert(result.returncode == 0, "exits 0", result.stderr)
    text = out.read_text() if out.exists() else ""
    # The rule the trust Makefile enforces: no assignment may still hold a placeholder
    # (the template's own comments mention them, which is fine).
    left_over = [line for line in text.splitlines() if re.match(r"^[A-Z0-9_]+=<run-make-", line)]
    _assert(bool(text) and not left_over, "no <run-make-…> placeholder left", "\n".join(left_over))
    kv = _kv(out) if out.exists() else {}

    print("identity_and_slot")
    _assert(kv.get("TRUST_CODE") == "SELFTEST", "TRUST_CODE=SELFTEST")
    _assert(kv.get("FL_KIT_SLOT") == "Trust_1" and kv.get("FL_KIT_SLOT_NUMBER") == "1", "slot Trust_1 / 1")
    _assert(kv.get("FL_BACKEND") == "nvflare", "FL_BACKEND follows --backend")

    print("aes_key_is_32_bytes")
    try:
        _assert(len(base64.b64decode(kv.get("AES_KEY_BASE64", ""))) == 32, "AES_KEY_BASE64 decodes to 32 bytes")
    except ValueError as exc:
        _assert(False, "AES_KEY_BASE64 is valid base64", str(exc))

    print("host_overrides")
    _assert(kv.get("NUM_AVAILABLE_GPUS") == "0" and kv.get("MEMORY_PER_GPU_IN_GIB") == "0", "CPU-only fl-client")
    _assert(kv.get("FL_KIT_DIR") == "/opt/kit", "FL_KIT_DIR from --fl-kit-dir")
    _assert(kv.get("OMOP_DATA_DIR") == "/opt/flip/data/trust-1/omop/db_data", "OMOP_DATA_DIR under the data root")
    _assert(
        kv.get("ORTHANC_STORAGE_DIR") == "/opt/flip/data/trust-1/orthanc-storage", "Orthanc storage under the data root"
    )
    _assert(kv.get("BASE_IMAGES_DOWNLOAD_DIR") == "/opt/flip/data/trust-1", "images base is the data root")
    _assert(kv.get("CENTRAL_HUB_API_URL", "").endswith(".invalid/api"), "hub URL can never resolve")

    print("file_mode_is_0600")
    _assert(out.exists() and (out.stat().st_mode & 0o777) == 0o600, "kit is 0600")

    print("refuses_overwrite")
    again = _run(out)
    _assert(again.returncode == 1 and "--force" in again.stderr, "refuses without --force", again.stderr)
    forced = _run(out, "--force")
    _assert(forced.returncode == 0, "overwrites with --force", forced.stderr)
    _assert(_kv(out).get("TRUST_API_KEY") != kv.get("TRUST_API_KEY"), "secrets are fresh on each run")

    print("rejects_unknown_backend")
    bad = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--backend",
            "pytorch",
            "--fl-kit-dir",
            "/k",
            "--data-root",
            "/d",
            "--out",
            str(tmp / "x"),
        ],
        capture_output=True,
        text=True,
    )
    _assert(bad.returncode != 0, "unknown backend rejected")

    print("leftover_placeholder_is_an_error")
    template = tmp / "template"
    template.write_text(TEMPLATE.read_text() + "NEW_MANAGED_KEY=<run-make-register-trust>\n")
    left = _run(tmp / "y", "--template", str(template))
    _assert(left.returncode == 1 and "NEW_MANAGED_KEY" in left.stderr, "names the unfilled key", left.stderr)

    print(f"\n==== {PASS} passed, {FAIL} failed ====")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
