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
"""Focused tests for the on-prem trust readiness checks.

Regression cover for the on-prem onboarding crash: a trust data dir
(OMOP_DATA_DIR / ORTHANC_STORAGE_DIR) owned by a container after a previous
`up` — the omop-db postgres image leaves PGDATA mode 0700 owned by its own
uid — made check_data_dir raise an uncaught PermissionError instead of
rendering a clean checklist row. The dir must now be reported as a
non-blocking WARN.

A real 0700 dir can't be exercised under CI's root user (root bypasses mode
bits), so the unreadable case monkeypatches Path.iterdir to raise
PermissionError. check_data_dir is loaded straight from the script via
importlib (the script is stdlib-only with a guarded __main__, so importing
it has no side effects).

Usage:
    uv run scripts/tests/test_onboard_onprem_trust.py
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
SCRIPT = SCRIPTS_DIR / "onboard_onprem_trust.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("onboard_onprem_trust", SCRIPT)
    assert spec, f"could not load {SCRIPT}"
    assert spec.loader, f"no loader for {SCRIPT}"
    module = importlib.util.module_from_spec(spec)
    # Register before exec so the @dataclass type resolution can find the
    # module in sys.modules (it looks up cls.__module__ there).
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mod = _load_module()

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


def _check(path: Path):
    """Run check_data_dir for an OMOP dir at an absolute path (kit present)."""
    return mod.check_data_dir(
        "OMOP data dir", "OMOP_DATA_DIR", "update-omop-data",
        {"OMOP_DATA_DIR": str(path)}, True, SCRIPTS_DIR.parent,
    )


def test_1_unreadable_dir_warns_not_crashes() -> None:
    """Container-owned dir we can't list -> non-blocking WARN, never raises/FAILs."""
    print("▶ unreadable (container-owned) data dir -> WARN, no crash")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td) / "db_data"
        d.mkdir()
        denied = PermissionError(13, "Permission denied")
        with mock.patch.object(mod.Path, "iterdir", side_effect=denied):
            try:
                result = _check(d)
                raised = False
            except Exception as exc:
                result = None
                raised = True
                print(f"    raised: {type(exc).__name__}: {exc}")
        _assert(not raised, "does not raise on PermissionError (the regression)")
        _assert(result is not None and result.status == mod.Status.WARN, "status is WARN (non-blocking)")
        _assert(result is not None and result.status != mod.Status.FAIL, "not a blocking FAIL")


def test_2_populated_dir_passes() -> None:
    """A readable, non-empty data dir -> PASS."""
    print("▶ populated, readable data dir -> PASS")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td) / "db_data"
        d.mkdir()
        (d / "PG_VERSION").write_text("16\n")
        result = _check(d)
        _assert(result.status == mod.Status.PASS, "status is PASS", result.detail)


def test_3_empty_dir_fails() -> None:
    """A readable but empty data dir -> FAIL with the populate hint."""
    print("▶ empty data dir -> FAIL")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td) / "db_data"
        d.mkdir()
        result = _check(d)
        _assert(result.status == mod.Status.FAIL, "status is FAIL", result.detail)
        _assert("empty" in result.detail, "detail mentions empty")


def test_4_missing_dir_fails() -> None:
    """A non-existent data dir -> FAIL with the does-not-exist message."""
    print("▶ missing data dir -> FAIL")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td) / "does_not_exist"
        result = _check(d)
        _assert(result.status == mod.Status.FAIL, "status is FAIL", result.detail)
        _assert("does not exist" in result.detail, "detail mentions missing")


def test_5_site_privacy_uses_runtime_validator() -> None:
    """The readiness check accepts a valid NVFLARE percentile policy."""
    print("▶ valid NVFLARE site privacy policy -> PASS")
    result = mod.check_site_privacy_policy(
        {"FL_BACKEND": "nvflare", "FL_SITE_PRIVACY_POLICY": "percentile"},
        True,
        "TEST",
        SCRIPTS_DIR.parent,
    )
    _assert(result.status == mod.Status.PASS, "status is PASS", result.detail)


def test_6_site_privacy_rejects_non_finite_value() -> None:
    """The readiness check inherits fail-closed numeric validation from the renderer."""
    print("▶ non-finite site privacy parameter -> FAIL")
    result = mod.check_site_privacy_policy(
        {
            "FL_BACKEND": "nvflare",
            "FL_SITE_PRIVACY_POLICY": "percentile",
            "FL_SITE_PRIVACY_GAMMA": "inf",
        },
        True,
        "TEST",
        SCRIPTS_DIR.parent,
    )
    _assert(result.status == mod.Status.FAIL, "status is FAIL", result.detail)


def test_7_site_privacy_rejects_unsupported_backend() -> None:
    """A configured policy must not appear active when Flower will ignore it."""
    print("▶ site privacy policy on Flower -> FAIL")
    result = mod.check_site_privacy_policy(
        {"FL_BACKEND": "flower", "FL_SITE_PRIVACY_POLICY": "percentile"},
        True,
        "TEST",
        SCRIPTS_DIR.parent,
    )
    _assert(result.status == mod.Status.FAIL, "status is FAIL", result.detail)
    _assert("ignored" in result.detail, "detail explains that Flower ignores the policy")


def main() -> None:
    if not SCRIPT.is_file():
        sys.exit(f"❌ {SCRIPT} not found")

    test_1_unreadable_dir_warns_not_crashes()
    test_2_populated_dir_passes()
    test_3_empty_dir_fails()
    test_4_missing_dir_fails()
    test_5_site_privacy_uses_runtime_validator()
    test_6_site_privacy_rejects_non_finite_value()
    test_7_site_privacy_rejects_unsupported_backend()

    print("—")
    print(f"PASS={PASS}  FAIL={FAIL}")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
