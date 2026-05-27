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
"""Black-box tests for scripts/sync_trust_kit.py.

Mirrors the contract of the old test_sync_trust_kits.sh (which targeted the
deprecated sync-trust-kits.sh + sync-trust-kit.sh shell pair) but exercises
the new env-driven, KIT-keyed Python interface.

Usage:
    uv run scripts/tests/test_sync_trust_kit.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
SYNC_SCRIPT = SCRIPTS_DIR / "sync_trust_kit.py"

# Minimal valid env: every HUB_SHARED_KEYS value populated.
HUB_SHARED_DEFAULTS: dict[str, str] = {
    "AES_KEY_BASE64": "v1==",  # trailing-= bug regression
    "CENTRAL_HUB_API_URL": "http://localhost:8080/api",
    "TRUST_API_KEY_HEADER": "Authorization",
    "FL_BACKEND": "flower",
    "FLOWER_KIT_DATE": "20260101",
    "FLARE_KIT_DATE": "20260102",
    "DOCKER_TAG": "stag",
    "DOCKER_REGISTRY": "ghcr.io/londonaicentre/",
    "DOCKER_FL_TAG": "stag",
    "DOCKER_FL_REGISTRY": "ghcr.io/londonaicentre/",
    "NLB_SUBDOMAIN": "fl.dev.flip.aicentre.co.uk",
    "FL_SERVER_PORT": "8002",
}

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


def _run(repo_root: Path, kit: str, env_overrides: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run sync_trust_kit.py against a temp repo root, return CompletedProcess."""
    env = {**HUB_SHARED_DEFAULTS, **(env_overrides or {}), "PATH": os.environ.get("PATH", "")}
    # Copy the script into the temp repo (symlinking won't work — the script
    # uses Path(__file__).resolve().parent.parent to discover the repo root,
    # which would follow a symlink straight back to the real repo).
    scripts_dir = repo_root / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    copy = scripts_dir / "sync_trust_kit.py"
    if not copy.exists():
        shutil.copy2(SYNC_SCRIPT, copy)
    return subprocess.run(
        ["uv", "run", "--no-config", str(copy), kit],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
    )


def test_1_appends_hub_shared_block() -> None:
    """Sync onto a legacy kit (no sentinel yet) — appends block, preserves creds."""
    print("▶ sync appends hub-shared block on legacy kit (preserves creds)")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "trust").mkdir()
        kit = root / "trust" / ".env.Trust_1"
        kit.write_text(
            "TRUST_API_KEY=preserved-key\n"
            "TRUST_INTERNAL_SERVICE_KEY=preserved-internal\n"
            "FL_KIT_SLOT=Trust_1\n"
            "FL_KIT_SLOT_NUMBER=1\n"
        )
        result = _run(root, "Trust_1")
        _assert(result.returncode == 0, "exit 0", result.stderr)
        content = kit.read_text()
        _assert("TRUST_API_KEY=preserved-key" in content, "credentials preserved")
        _assert("TRUST_INTERNAL_SERVICE_KEY=preserved-internal" in content, "internal service key preserved")
        _assert("AES_KEY_BASE64=v1==" in content, "trailing = preserved (regression)")
        _assert("Hub-shared (managed" in content, "sentinel header added")
        for key, value in HUB_SHARED_DEFAULTS.items():
            _assert(f"{key}={value}" in content, f"{key} synced")


def test_2_idempotent_rotation() -> None:
    """Second sync with new values rotates them in-place — no dupes, creds untouched."""
    print("▶ sync rotates values idempotently, never touches creds")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "trust").mkdir()
        kit = root / "trust" / ".env.Trust_1"
        kit.write_text("TRUST_API_KEY=preserved-key\n")

        _run(root, "Trust_1")
        _run(root, "Trust_1", env_overrides={"AES_KEY_BASE64": "v2==", "FL_BACKEND": "nvflare"})

        content = kit.read_text()
        _assert("AES_KEY_BASE64=v2==" in content, "AES_KEY rotated to v2")
        _assert("FL_BACKEND=nvflare" in content, "FL_BACKEND rotated")
        _assert("TRUST_API_KEY=preserved-key" in content, "creds still untouched")
        _assert(content.count("AES_KEY_BASE64=") == 1, "no duplicate AES_KEY_BASE64")
        _assert(content.count("Hub-shared") == 1, "exactly one Hub-shared header")


def test_3_missing_kit_file_is_error() -> None:
    """Sync against a slot whose kit file doesn't exist exits 1, never creates it."""
    print("▶ sync errors on missing kit file (does not create it)")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "trust").mkdir()
        result = _run(root, "Trust_1")
        _assert(result.returncode != 0, "exit non-zero")
        _assert(not (root / "trust" / ".env.Trust_1").exists(), "missing kit file not created")
        _assert("not found" in result.stderr, "stderr explains why")


def test_4_missing_env_var_is_error() -> None:
    """Sync errors if any required Hub-shared key is unset in the environment."""
    print("▶ sync errors when env vars are missing")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "trust").mkdir()
        kit = root / "trust" / ".env.Trust_1"
        kit.write_text("TRUST_API_KEY=preserved\n")

        env = {k: v for k, v in HUB_SHARED_DEFAULTS.items() if k != "AES_KEY_BASE64"}
        env["PATH"] = os.environ.get("PATH", "")
        result = subprocess.run(
            ["uv", "run", "--no-config", str(SYNC_SCRIPT), "Trust_1"],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
        )
        _assert(result.returncode != 0, "exit non-zero")
        _assert("AES_KEY_BASE64" in result.stderr, "stderr names the missing key")
        _assert("AES_KEY_BASE64=" not in kit.read_text(), "kit file not mutated on error")


def test_5_in_place_replacement_on_template() -> None:
    """Sync replaces placeholder values in their original positions (template flow)."""
    print("▶ sync replaces <run-make-sync-trust-kit> placeholders in place")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "trust").mkdir()
        kit = root / "trust" / ".env.Trust_1"
        kit.write_text(
            "# host-local section\n"
            "OMOP_DB_PORT=5436\n"
            "AES_KEY_BASE64=<run-make-sync-trust-kit>\n"
            "CENTRAL_HUB_API_URL=<run-make-sync-trust-kit>\n"
            "# Kit credentials section\n"
            "TRUST_API_KEY=preserved\n"
        )
        _run(root, "Trust_1")
        lines = kit.read_text().splitlines()
        # AES_KEY_BASE64 line must still be at index 2 (between OMOP_DB_PORT and CENTRAL_HUB).
        _assert(lines[2] == "AES_KEY_BASE64=v1==", f"AES_KEY_BASE64 in-place (line 2)")
        # Comment + creds line preserved.
        _assert("# Kit credentials section" in lines, "comment preserved")
        _assert("TRUST_API_KEY=preserved" in lines, "creds preserved")


def main() -> None:
    if not SYNC_SCRIPT.is_file():
        sys.exit(f"❌ {SYNC_SCRIPT} not found")
    if shutil.which("uv") is None:
        sys.exit("❌ uv not on PATH — install uv before running these tests")

    test_1_appends_hub_shared_block()
    test_2_idempotent_rotation()
    test_3_missing_kit_file_is_error()
    test_4_missing_env_var_is_error()
    test_5_in_place_replacement_on_template()

    print("—")
    print(f"PASS={PASS}  FAIL={FAIL}")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
