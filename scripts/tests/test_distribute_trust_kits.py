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
"""Black-box tests for scripts/distribute_trust_kits.py.

Pins the ``--target PATH`` contract (one kit JSON on stdin → that kit file) that
the dev register-trust pipe and deploy/providers/AWS/scripts/register-trusts.sh use.

Usage:
    uv run --no-config scripts/tests/test_distribute_trust_kits.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
DISTRIBUTE = SCRIPTS_DIR / "distribute_trust_kits.py"
LIB = SCRIPTS_DIR / "trust_kit_lib.py"

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
    """Create a temp repo with scripts/ (copies of the script + lib) and trust/."""
    root = Path(tempfile.mkdtemp())
    (root / "scripts").mkdir()
    (root / "trust").mkdir()
    shutil.copy2(DISTRIBUTE, root / "scripts" / "distribute_trust_kits.py")
    shutil.copy2(LIB, root / "scripts" / "trust_kit_lib.py")
    return root


def _run(root: Path, stdin: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python3", str(root / "scripts" / "distribute_trust_kits.py"), *args],
        cwd=root,
        input=stdin,
        env={**os.environ, "PATH": os.environ.get("PATH", "")},
        capture_output=True,
        text=True,
    )


def _full_kit() -> dict:
    return {
        "trust_id": "abc",
        "trust_name": "GSTT",
        "trust_api_key": "plain-api-1",  # pragma: allowlist secret
        "trust_internal_service_key": "plain-int-1",  # pragma: allowlist secret
        "fl_kit_slot": "Trust_1",
        "fl_kit_slot_number": 1,
        "hub_shared": {"AES_KEY_BASE64": "Zm9vYmFy", "FL_BACKEND": "flower"},
    }


def test_target_is_required() -> None:
    print("▶ no --target: refused (there is no slot-named default path any more)")
    root = _temp_repo()
    try:
        res = _run(root, json.dumps([_full_kit()]))
        _assert(res.returncode != 0, "non-zero exit without --target", res.stdout)
        _assert(not list((root / "trust").iterdir()), "no kit file written")
    finally:
        shutil.rmtree(root)


def test_target_mode_empty_is_noop() -> None:
    print("▶ --target mode: empty array is a no-op (exit 0, no file)")
    root = _temp_repo()
    try:
        target = root / "trust" / ".env.DEMO.prod"
        res = _run(root, "[]", "--target", str(target))
        _assert(res.returncode == 0, "exit 0 on empty array", res.stderr)
        _assert(not target.exists(), "no kit file written")
    finally:
        shutil.rmtree(root)


def test_target_mode_skip_preserves_creds() -> None:
    print("▶ --target mode: skip-path kit (no creds) preserves existing creds, refreshes hub-shared")
    root = _temp_repo()
    try:
        target = root / "trust" / ".env.DEMO.prod"
        target.write_text("TRUST_API_KEY=preserved\n")
        skip = {
            "trust_id": "abc",
            "trust_name": "GSTT",
            "fl_kit_slot": "Trust_1",
            "fl_kit_slot_number": 1,
            "hub_shared": {"AES_KEY_BASE64": "Zm9vYmFy"},
        }
        _run(root, json.dumps(skip), "--target", str(target))
        content = target.read_text()
        _assert("TRUST_API_KEY=preserved" in content, "existing creds preserved")
        _assert("AES_KEY_BASE64=Zm9vYmFy" in content, "hub-shared upserted on skip path")
    finally:
        shutil.rmtree(root)


def test_target_mode_seeds_from_example() -> None:
    print("▶ --target mode: seeds host-local profile from --example on first write")
    root = _temp_repo()
    try:
        target = root / "trust" / ".env.DEMO.prod"
        example = root / "trust" / ".env.DEMO.prod.example"
        example.write_text("# host-local\nOMOP_DB_PORT=5436\n")
        _run(root, json.dumps(_full_kit()), "--target", str(target), "--example", str(example))
        content = target.read_text()
        _assert("OMOP_DB_PORT=5436" in content, "host-local seeded from example")
        _assert("TRUST_API_KEY=plain-api-1" in content, "creds appended over seeded example")
    finally:
        shutil.rmtree(root)


def test_target_mode_writes_single_kit() -> None:
    print("▶ --target mode: writes one kit on stdin to the given path")
    root = _temp_repo()
    try:
        target = root / "trust" / ".env.DEMO.prod"
        res = _run(root, json.dumps(_full_kit()), "--target", str(target))
        _assert(res.returncode == 0, "exit 0", res.stderr)
        _assert(target.exists(), "kit written to --target path")
        content = target.read_text()
        _assert("TRUST_API_KEY=plain-api-1" in content, "creds written")
        _assert("AES_KEY_BASE64=Zm9vYmFy" in content, "prod target writes hub-shared LIVE")
    finally:
        shutil.rmtree(root)


def test_target_mode_dev_comments_hub_shared() -> None:
    print("▶ --target .development: hub-shared written commented (inert), not a live copy")
    root = _temp_repo()
    try:
        target = root / "trust" / ".env.GSTT.development"
        res = _run(root, json.dumps(_full_kit()), "--target", str(target))
        _assert(res.returncode == 0, "exit 0", res.stderr)
        content = target.read_text()
        _assert("# AES_KEY_BASE64=" in content, "hub-shared key commented in dev kit")
        live = [
            ln
            for ln in content.splitlines()
            if not ln.lstrip().startswith("#") and ln.split("=", 1)[0] == "AES_KEY_BASE64"
        ]
        _assert(not live, "no live hub-shared line in dev kit", str(live))
        _assert("TRUST_API_KEY=plain-api-1" in content, "credentials still written in dev")
    finally:
        shutil.rmtree(root)


def main() -> None:
    if not DISTRIBUTE.is_file():
        sys.exit(f"❌ {DISTRIBUTE} not found")
    test_target_is_required()
    test_target_mode_empty_is_noop()
    test_target_mode_skip_preserves_creds()
    test_target_mode_seeds_from_example()
    test_target_mode_writes_single_kit()
    test_target_mode_dev_comments_hub_shared()
    print("—")
    print(f"PASS={PASS}  FAIL={FAIL}")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
