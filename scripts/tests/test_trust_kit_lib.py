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
"""Unit tests for scripts/trust_kit_lib.py — the single kit-file writer.

trust_kit_lib.write_kit() merges a kit dict (as emitted by
flip_api.scripts.register_trust) into trust/.env.<...> in place, preserving
operator edits. It is the single implementation behind the dev distributor,
the prod distributor, and sync — so these tests pin the behaviours the old
distribute-trust-kits.sh and sync_trust_kit.py relied on.

Usage:
    uv run --no-config scripts/tests/test_trust_kit_lib.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import trust_kit_lib as tkl  # noqa: E402

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


def _full_kit(**overrides: object) -> dict:
    kit = {
        "trust_id": "11111111-1111-1111-1111-111111111111",
        "trust_name": "GSTT Hospital",
        "trust_api_key": "plain-api-key",
        "trust_internal_service_key": "plain-internal-key",
        "fl_kit_slot": "Trust_1",
        "fl_kit_slot_number": 1,
        "hub_shared": {"AES_KEY_BASE64": "v1==", "FL_BACKEND": "flower"},
    }
    kit.update(overrides)
    return kit


def test_new_kit_writes_creds_meta_and_hub_shared() -> None:
    print("▶ write_kit on absent target seeds from example + writes creds/meta/hub-shared")
    with tempfile.TemporaryDirectory() as td:
        trust = Path(td)
        example = trust / ".env.GSTT.development.example"
        example.write_text("# host-local\nOMOP_DB_PORT=5436\n")
        target = trust / ".env.GSTT.development"

        tkl.write_kit(target, _full_kit(), example=example)

        content = target.read_text()
        _assert("OMOP_DB_PORT=5436" in content, "host-local profile seeded from example")
        _assert("TRUST_API_KEY=plain-api-key" in content, "TRUST_API_KEY written")
        _assert("TRUST_INTERNAL_SERVICE_KEY=plain-internal-key" in content, "internal key written")
        _assert("EXPECTED_TRUST_ID=11111111-1111-1111-1111-111111111111" in content, "EXPECTED_TRUST_ID written")
        _assert("FL_KIT_SLOT=Trust_1" in content, "FL_KIT_SLOT written")
        _assert("FL_KIT_SLOT_NUMBER=1" in content, "FL_KIT_SLOT_NUMBER written")
        _assert(tkl.SENTINEL in content, "hub-shared sentinel present")
        _assert("AES_KEY_BASE64=v1==" in content, "hub-shared AES key written (trailing = preserved)")
        _assert("FL_BACKEND=flower" in content, "hub-shared FL_BACKEND written")
        _assert((target.stat().st_mode & 0o777) == 0o600, "kit file chmod 600")


def test_existing_world_readable_kit_is_tightened_before_creds_land() -> None:
    print("▶ write_kit tightens an existing 0644 kit file before writing credentials into it")
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / ".env.GSTT.development"
        target.write_text("TRUST_API_KEY=old-key\n")
        target.chmod(0o644)

        seen_at_chmod: dict[str, object] = {}
        real_fchmod = os.fchmod

        def spy(fd: int, mode: int) -> None:
            # Path.write_text + chmod would already have written the new credentials by now; the
            # fd-based write must still show the OLD contents at the moment the mode is tightened.
            seen_at_chmod["mode"] = mode
            seen_at_chmod["content"] = target.read_text()
            real_fchmod(fd, mode)

        with mock.patch.object(os, "fchmod", spy):
            tkl.write_kit(target, _full_kit())

        # .get, not [...]: write_text-then-chmod never calls os.fchmod at all, and that must read as
        # a failed assertion rather than a KeyError.
        _assert(seen_at_chmod.get("mode") == 0o600, "fchmod called with 0600 before writing")
        _assert(
            "plain-api-key" not in str(seen_at_chmod.get("content", "plain-api-key")),
            "new credentials absent from disk until after the mode is tightened",
        )
        _assert("TRUST_API_KEY=plain-api-key" in target.read_text(), "credentials written after chmod")
        _assert((target.stat().st_mode & 0o777) == 0o600, "kit file ends at 0600")


def test_skip_path_preserves_existing_creds() -> None:
    print("▶ skip-path kit (no creds) preserves existing creds, refreshes hub-shared")
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / ".env.GSTT.development"
        target.write_text(
            "TRUST_API_KEY=preserved-key\n"
            "TRUST_INTERNAL_SERVICE_KEY=preserved-internal\n"
            "FL_KIT_SLOT=Trust_1\n"
            "FL_KIT_SLOT_NUMBER=1\n"
        )
        skip_kit = {
            "trust_id": "11111111-1111-1111-1111-111111111111",
            "trust_name": "GSTT Hospital",
            "fl_kit_slot": "Trust_1",
            "fl_kit_slot_number": 1,
            "hub_shared": {"AES_KEY_BASE64": "rotated=="},
        }
        tkl.write_kit(target, skip_kit)

        content = target.read_text()
        _assert("TRUST_API_KEY=preserved-key" in content, "TRUST_API_KEY preserved on skip path")
        _assert("TRUST_INTERNAL_SERVICE_KEY=preserved-internal" in content, "internal key preserved on skip path")
        _assert("AES_KEY_BASE64=rotated==" in content, "hub-shared refreshed on skip path")


def test_idempotent_rotation_no_dupes() -> None:
    print("▶ second write rotates hub-shared in place — no dupes, one sentinel, creds untouched")
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / ".env.GSTT.development"
        tkl.write_kit(target, _full_kit())
        tkl.write_kit(target, _full_kit(hub_shared={"AES_KEY_BASE64": "v2==", "FL_BACKEND": "nvflare"}))

        content = target.read_text()
        _assert(content.count("AES_KEY_BASE64=") == 1, "no duplicate AES_KEY_BASE64")
        _assert("AES_KEY_BASE64=v2==" in content, "AES key rotated to v2")
        _assert("FL_BACKEND=nvflare" in content, "FL_BACKEND rotated")
        _assert(content.count(tkl.SENTINEL) == 1, "exactly one sentinel")
        _assert(content.count("TRUST_API_KEY=") == 1, "no duplicate TRUST_API_KEY")


def test_absent_target_no_example_creates_file() -> None:
    print("▶ write_kit with absent target and no example creates the file with managed content")
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / ".env.DEMO.production"
        tkl.write_kit(target, _full_kit())
        _assert(target.exists(), "file created")
        _assert("TRUST_API_KEY=plain-api-key" in target.read_text(), "creds written into new file")


def test_ec2_rerun_preserves_host_local_profile() -> None:
    print("▶ ec2 re-run: pre-seeded host-local + old creds; skip kit preserves both, refreshes hub-shared")
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / ".env.DEMO.production"
        target.write_text(
            "# Host-local profile\n"
            "LOKI_PORT=3201\n"
            "GRAFANA_PORT=3301\n"
            "TRUST_API_KEY=existing-prod-key\n"
            "FL_KIT_SLOT=Trust_2\n"
            "FL_KIT_SLOT_NUMBER=2\n"
            f"{tkl.SENTINEL}\n"
            "AES_KEY_BASE64=old==\n"
        )
        skip_kit = {
            "trust_id": "22222222-2222-2222-2222-222222222222",
            "trust_name": "DEMO",
            "fl_kit_slot": "Trust_2",
            "fl_kit_slot_number": 2,
            "hub_shared": {"AES_KEY_BASE64": "new=="},
        }
        tkl.write_kit(target, skip_kit)

        content = target.read_text()
        _assert("LOKI_PORT=3201" in content, "host-local LOKI_PORT preserved")
        _assert("GRAFANA_PORT=3301" in content, "host-local GRAFANA_PORT preserved")
        _assert("TRUST_API_KEY=existing-prod-key" in content, "existing prod creds preserved")
        _assert("AES_KEY_BASE64=new==" in content, "hub-shared rotated")
        _assert(content.count("AES_KEY_BASE64=") == 1, "no duplicate AES_KEY_BASE64 after re-run")


def test_dev_commented_hub_shared() -> None:
    print("▶ write_kit(hub_shared_commented=True) comments the whole hub-shared block (no live values)")
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / ".env.GSTT.development"
        # Seed with a host-local line + a STALE live hub-shared line (the drift we're killing).
        target.write_text("OMOP_DB_PORT=5436\nAES_KEY_BASE64=STALE_LIVE_VALUE\n")

        tkl.write_kit(target, _full_kit(), hub_shared_commented=True)
        content = target.read_text()
        lines = content.splitlines()

        _assert(tkl.SENTINEL in content, "sentinel present")
        for key in tkl.HUB_SHARED_KEYS:
            _assert(f"# {key}=" in content, f"{key} written commented")
            live = [ln for ln in lines if not ln.lstrip().startswith("#") and ln.split("=", 1)[0] == key]
            _assert(not live, f"{key} has no live line", detail=f"found: {live}")
        _assert("AES_KEY_BASE64=STALE_LIVE_VALUE" not in content, "stale live hub-shared value commented away")
        _assert("OMOP_DB_PORT=5436" in content, "host-local preserved")
        _assert("TRUST_API_KEY=plain-api-key" in content, "credentials still written in dev")

        tkl.write_kit(target, _full_kit(), hub_shared_commented=True)
        content2 = target.read_text()
        _assert(content2.count(tkl.SENTINEL) == 1, "no duplicate sentinel on re-run")
        _assert(content2.count("# AES_KEY_BASE64=") == 1, "no duplicate commented key on re-run")


def main() -> None:
    test_new_kit_writes_creds_meta_and_hub_shared()
    test_existing_world_readable_kit_is_tightened_before_creds_land()
    test_dev_commented_hub_shared()
    test_skip_path_preserves_existing_creds()
    test_idempotent_rotation_no_dupes()
    test_absent_target_no_example_creates_file()
    test_ec2_rerun_preserves_host_local_profile()
    print("—")
    print(f"PASS={PASS}  FAIL={FAIL}")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
