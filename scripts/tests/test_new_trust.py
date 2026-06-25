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
"""Black-box tests for scripts/new_trust.py — the kit scaffolder.

new_trust.py copies the base kit template and injects the trust's identity
(TRUST_NAME / TRUST_CODE / TRUST_REGION), producing trust/.env.<CODE>.<env>
ready for `make register-trust` to fill the managed blocks. It refuses to
overwrite an existing kit.

Usage:
    uv run --no-config scripts/tests/test_new_trust.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
NEW_TRUST = SCRIPTS_DIR / "new_trust.py"

PASS = 0
FAIL = 0


def _assert(condition: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        print(f"  ✅ {label}")
        PASS += 1
    else:
        print(f"  ❌ {label}")
        if detail:
            for line in detail.splitlines():
                print(f"    {line}")
        FAIL += 1


def _temp_repo() -> Path:
    root = Path(tempfile.mkdtemp())
    (root / "scripts").mkdir()
    (root / "trust").mkdir()
    shutil.copy2(NEW_TRUST, root / "scripts" / "new_trust.py")
    # Minimal base template with a host-local default + managed placeholders.
    (root / "trust" / ".env.example").write_text(
        "# Host-local profile\n"
        "OMOP_DB_PORT=5436\n"
        "ORTHANC_PASSWORD=mock-orthanc-pw\n"
        "TRUST_API_KEY=<run-make-register-trust>\n"
    )
    return root


def _run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python3", str(root / "scripts" / "new_trust.py"), *args],
        cwd=root,
        env={**os.environ, "PATH": os.environ.get("PATH", "")},
        capture_output=True,
        text=True,
    )


def test_scaffolds_kit_with_identity_and_defaults() -> None:
    print("▶ scaffolds trust/.env.<CODE>.<env> with identity + template defaults + placeholders")
    root = _temp_repo()
    try:
        res = _run(root, "--code", "DEMO", "--name", "DEMO Hospital", "--region", "London",
                   "--env", "production")
        target = root / "trust" / ".env.DEMO.production"
        _assert(res.returncode == 0, "exit 0", res.stderr)
        _assert(target.exists(), "kit scaffolded at .env.<CODE>.<env>")
        content = target.read_text() if target.exists() else ""
        _assert("TRUST_NAME=DEMO Hospital" in content, "TRUST_NAME injected")
        _assert("TRUST_CODE=DEMO" in content, "TRUST_CODE injected")
        _assert("TRUST_REGION=London" in content, "TRUST_REGION injected")
        _assert("TRUST_HOST=" not in content, "TRUST_HOST not written to kit (delivery is command-driven)")
        _assert("OMOP_DB_PORT=5436" in content, "host-local default from template carried over")
        _assert("ORTHANC_PASSWORD=mock-orthanc-pw" in content, "mock-stack cred from template carried over")
        _assert("TRUST_API_KEY=<run-make-register-trust>" in content, "managed placeholder preserved")
        _assert((target.stat().st_mode & 0o777) == 0o600, "kit file chmod 600")
    finally:
        shutil.rmtree(root)


def test_refuses_to_overwrite() -> None:
    print("▶ refuses to overwrite an existing kit")
    root = _temp_repo()
    try:
        (root / "trust" / ".env.DEMO.production").write_text("EXISTING=keep-me\n")
        res = _run(root, "--code", "DEMO", "--name", "DEMO", "--env", "production")
        _assert(res.returncode != 0, "exit non-zero", res.stdout)
        _assert("EXISTING=keep-me" in (root / "trust" / ".env.DEMO.production").read_text(), "existing kit untouched")
    finally:
        shutil.rmtree(root)


def test_region_optional() -> None:
    print("▶ region is optional — omitted when not provided")
    root = _temp_repo()
    try:
        res = _run(root, "--code", "GSTT", "--name", "GSTT", "--env", "development")
        target = root / "trust" / ".env.GSTT.development"
        _assert(res.returncode == 0, "exit 0", res.stderr)
        content = target.read_text() if target.exists() else ""
        _assert("TRUST_CODE=GSTT" in content, "TRUST_CODE injected")
        _assert("TRUST_REGION=" not in content, "TRUST_REGION omitted when not given")
    finally:
        shutil.rmtree(root)


def main() -> None:
    if not NEW_TRUST.is_file():
        sys.exit(f"❌ {NEW_TRUST} not found")
    test_scaffolds_kit_with_identity_and_defaults()
    test_refuses_to_overwrite()
    test_region_optional()
    print("—")
    print(f"PASS={PASS}  FAIL={FAIL}")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
