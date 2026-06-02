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
"""Unit tests for scripts/check_local_status.py kit discovery.

discover_trust_kits() globs trust/.env.<CODE>.<env> kit files and reads each
trust's host-facing ports + assigned FL slot, so the status checker reflects
the CODE-named kit architecture instead of fixed Trust_1 / Trust_2 names.

Usage:
    uv run --no-config scripts/tests/test_check_local_status.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

from check_local_status import TrustKit, discover_trust_kits  # noqa: E402

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
            print(f"    {detail}")
        FAIL += 1


def _write_kit(trust_dir: Path, code: str, env: str, **kv: object) -> None:
    body = "\n".join(f"{k}={v}" for k, v in kv.items())
    (trust_dir / f".env.{code}.{env}").write_text(body + "\n")


def _trust_dir() -> Path:
    trust = Path(tempfile.mkdtemp()) / "trust"
    trust.mkdir()
    return trust


def test_discovers_code_kits() -> None:
    trust = _trust_dir()
    _write_kit(
        trust,
        "KCH",
        "development",
        TRUST_NAME="KCH",
        FL_KIT_SLOT_NUMBER=1,
        XNAT_PORT=8106,
        PACS_UI_PORT=8044,
        TRUST_API_PORT=8020,
        IMAGING_API_PORT=8001,
        DATA_ACCESS_API_PORT=8010,
    )
    _write_kit(
        trust, "GSTT", "development", TRUST_NAME="GSTT", FL_KIT_SLOT_NUMBER=2, XNAT_PORT=8104, PACS_UI_PORT=8042
    )
    kits = discover_trust_kits(trust, "development")
    _assert(len(kits) == 2, "discovers both CODE kits", f"got {len(kits)}")
    by_code = {k.code: k for k in kits}
    _assert("KCH" in by_code and "GSTT" in by_code, "keyed by CODE", f"got {sorted(by_code)}")
    kch = by_code.get("KCH", TrustKit("", "", None, None, None, None, None, None))
    _assert(kch.slot_number == 1, "reads FL_KIT_SLOT_NUMBER", f"got {kch.slot_number!r}")
    _assert(kch.xnat_port == "8106" and kch.pacs_ui_port == "8044", "reads XNAT/PACS ports")
    _assert(kch.trust_api_port == "8020", "reads TRUST_API_PORT", f"got {kch.trust_api_port!r}")


def test_ignores_examples_and_other_envs() -> None:
    trust = _trust_dir()
    _write_kit(trust, "KCH", "development", TRUST_NAME="KCH", FL_KIT_SLOT_NUMBER=1)
    (trust / ".env.KCH.development.example").write_text("TRUST_NAME=SHOULD_IGNORE\n")
    _write_kit(trust, "PROD1", "production", TRUST_NAME="PROD1", FL_KIT_SLOT_NUMBER=1)
    kits = discover_trust_kits(trust, "development")
    _assert(
        len(kits) == 1 and kits[0].code == "KCH",
        "ignores .example + other-env kits",
        f"got {[k.code for k in kits]}",
    )


def test_missing_slot_and_name_fallback() -> None:
    trust = _trust_dir()
    _write_kit(trust, "KCL", "development", XNAT_PORT=8108)  # no TRUST_NAME, no slot
    kits = discover_trust_kits(trust, "development")
    _assert(len(kits) == 1, "discovers kit without name/slot", f"got {len(kits)}")
    _assert(kits[0].slot_number is None, "slot_number None when absent", f"got {kits[0].slot_number!r}")
    _assert(kits[0].name == "KCL", "name falls back to CODE", f"got {kits[0].name!r}")


def test_missing_dir_returns_empty() -> None:
    kits = discover_trust_kits(Path(tempfile.mkdtemp()) / "nope", "development")
    _assert(kits == [], "missing trust dir -> empty list", f"got {kits!r}")


def main() -> None:
    for test in (
        test_discovers_code_kits,
        test_ignores_examples_and_other_envs,
        test_missing_slot_and_name_fallback,
        test_missing_dir_returns_empty,
    ):
        print(f"\n{test.__name__}")
        test()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
