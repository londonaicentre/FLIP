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
import io
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
        "OMOP data dir", "OMOP_DATA_DIR",
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


def test_3_empty_dir_warns() -> None:
    """A readable but empty data dir -> WARN: up-trust seeds it (FLIP#1187), so it is not a blocker."""
    print("▶ empty data dir -> FAIL")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td) / "db_data"
        d.mkdir()
        result = _check(d)
        _assert(result.status == mod.Status.WARN, "status is WARN", result.detail)
        _assert("empty" in result.detail, "detail mentions empty")
        _assert(any("up-trust" in h for h in result.hints), "hint names the seeding target")


def test_4_missing_dir_warns() -> None:
    """A non-existent data dir -> FAIL with the does-not-exist message."""
    print("▶ missing data dir -> FAIL")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td) / "does_not_exist"
        result = _check(d)
        _assert(result.status == mod.Status.WARN, "status is WARN", result.detail)
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


def test_8_site_privacy_not_configured_reports_cleanly() -> None:
    """A kit with no FL_SITE_PRIVACY_* vars passes without claiming a stale render exists."""
    print("▶ no site privacy policy configured -> PASS, clean detail")
    result = mod.check_site_privacy_policy(
        {"FL_BACKEND": "nvflare"},
        True,
        "TEST",
        SCRIPTS_DIR.parent,
    )
    _assert(result.status == mod.Status.PASS, "status is PASS", result.detail)
    _assert("no site privacy policy configured" in result.detail, "detail names the no-policy state", result.detail)
    _assert("remove" not in result.detail, "detail does not claim a stale render", result.detail)
    _assert("/dev/null" not in result.detail, "detail names no validation path", result.detail)


def test_9_site_privacy_rejects_misspelt_variable() -> None:
    """A typo'd FL_SITE_PRIVACY_* kit var must FAIL here rather than run a weaker filter than intended.

    This check is where the renderer's unknown-name guard actually bites: it forwards every
    FL_SITE_PRIVACY_-prefixed kit key to the renderer, whereas the compose and Helm delivery paths
    enumerate only the three known names and drop a typo before it ever reaches the container.
    """
    print("▶ misspelt site privacy variable -> FAIL")
    result = mod.check_site_privacy_policy(
        {
            "FL_BACKEND": "nvflare",
            "FL_SITE_PRIVACY_POLICY": "percentile",
            "FL_SITE_PRIVACY_PERCENTIL": "25",
        },
        True,
        "TEST",
        SCRIPTS_DIR.parent,
    )
    _assert(result.status == mod.Status.FAIL, "status is FAIL", result.detail)
    _assert("FL_SITE_PRIVACY_PERCENTIL " in result.detail, "detail names the misspelt variable", result.detail)


def _gpu_check(kit_vars: dict[str, str], host_gpus: int | None):
    """Run check_gpu_capacity with the host GPU probe stubbed (kit present)."""
    with mock.patch.object(mod, "detect_host_gpu_count", return_value=host_gpus) as probe:
        result = mod.check_gpu_capacity(kit_vars, True, "TEST")
    return result, probe


def test_10_gpu_unset_on_nvflare_warns_without_probing() -> None:
    """An unset NUM_AVAILABLE_GPUS is not CPU-only: the GPU overlay is skipped but fl-client defaults to 1.

    The Makefile and the entrypoint disagree on the default, so the container crash-loops whatever
    the host carries — the check must WARN outright rather than consult the host GPU count.
    """
    print("▶ NUM_AVAILABLE_GPUS unset (nvflare) -> WARN, host not probed")
    result, probe = _gpu_check({"FL_BACKEND": "nvflare"}, host_gpus=1)
    _assert(result.status == mod.Status.WARN, "status is WARN, not a false CPU-only PASS", result.detail)
    _assert(not probe.called, "host GPU count is irrelevant and was not consulted")
    _assert(
        "unset" in result.detail and "defaults to 1" in result.detail,
        "detail explains the mismatch",
        result.detail,
    )
    _assert(any("explicitly" in h for h in result.hints), "hint tells the operator to set it explicitly")


def test_11_gpu_unset_on_flower_is_cpu_only() -> None:
    """The Flower client never reads NUM_AVAILABLE_GPUS, so an unset value is a plain CPU-only PASS there."""
    print("▶ NUM_AVAILABLE_GPUS unset (flower) -> CPU-only PASS")
    result, probe = _gpu_check({"FL_BACKEND": "flower"}, host_gpus=0)
    _assert(result.status == mod.Status.PASS, "status is PASS", result.detail)
    _assert("CPU-only" in result.detail, "detail says CPU-only", result.detail)
    _assert(not probe.called, "host GPU count not consulted")


def test_12_gpu_explicit_zero_is_cpu_only_pass() -> None:
    """An explicit 0 is CPU-only and must not probe the host."""
    print("▶ NUM_AVAILABLE_GPUS=0 -> CPU-only PASS without probing the host")
    result, probe = _gpu_check({"FL_BACKEND": "nvflare", "NUM_AVAILABLE_GPUS": "0"}, host_gpus=0)
    _assert(result.status == mod.Status.PASS, "status is PASS", result.detail)
    _assert("CPU-only" in result.detail, "detail says CPU-only", result.detail)
    _assert(not probe.called, "host GPU count not consulted for an explicit 0")


def test_13_gpu_explicit_one_unchanged() -> None:
    """An explicit 1 behaves as before: WARN on a GPU-less host, PASS when the host exposes one."""
    print("▶ NUM_AVAILABLE_GPUS=1 -> WARN on 0 host GPUs, PASS on 1")
    warn, probe = _gpu_check({"FL_BACKEND": "nvflare", "NUM_AVAILABLE_GPUS": "1"}, host_gpus=0)
    _assert(warn.status == mod.Status.WARN, "0 host GPUs -> WARN", warn.detail)
    _assert(probe.called, "host GPU count was consulted for an explicit value")
    _assert("NUM_AVAILABLE_GPUS=1" in warn.detail, "detail names the explicit value", warn.detail)
    ok, _ = _gpu_check({"FL_BACKEND": "nvflare", "NUM_AVAILABLE_GPUS": "1"}, host_gpus=1)
    _assert(ok.status == mod.Status.PASS, "1 host GPU -> PASS", ok.detail)


HUB_KEY_B64 = "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE="  # 32 x 0x01  # pragma: allowlist secret
HUB_KEY_FP = "72cd6e8422c4"  # sha256(32 x 0x01)[:12] — the hub's and trust-api's formula
STALE_KEY_B64 = "AgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgI="  # 32 x 0x02  # pragma: allowlist secret


def _hub_shared_current(kit_vars: dict, health):
    """Run check_hub_shared_current with trust-api's /health answering ``health`` (a dict, or an exception)."""
    if isinstance(health, BaseException):
        patched = mock.patch.object(mod, "fetch_local_trust_health", side_effect=health)
    else:
        patched = mock.patch.object(mod, "fetch_local_trust_health", return_value=health)
    with patched:
        return mod.check_hub_shared_current(kit_vars, True)


def test_14_hub_shared_current_flags_a_stale_key() -> None:
    """trust-api says its key no longer matches the hub's -> FAIL naming the refreshed-kit fix (FLIP#1204)."""
    print("▶ hub-shared currency: stale AES key -> FAIL")
    kit = {"TRUST_API_PORT": "8020", "DOCKER_TAG": "v0.6.0", "AES_KEY_BASE64": STALE_KEY_B64}
    result = _hub_shared_current(
        kit, {"hub_version": "v0.6.0", "hub_key_match": False, "hub_key_fingerprint": HUB_KEY_FP}
    )
    _assert(result.status == mod.Status.FAIL, "status is FAIL")
    _assert(any("sync-trust-kit" in h for h in result.hints), "hint names the admin-side re-sync")


def test_15_hub_shared_current_passes_and_notes_the_release_gap() -> None:
    """Key matches: PASS. Kit pinned behind the hub: WARN naming upgrade-onprem-trust, never FAIL
    (the upgrade verb runs this checklist first, so a FAIL here would make it un-runnable)."""
    print("▶ hub-shared currency: key matches -> PASS; kit behind the hub -> WARN")
    kit = {"TRUST_API_PORT": "8020", "DOCKER_TAG": "v0.6.0", "AES_KEY_BASE64": HUB_KEY_B64}
    matched = {"hub_key_match": True, "hub_key_fingerprint": HUB_KEY_FP}
    ok = _hub_shared_current(kit, {"version": "v0.6.0", "hub_version": "v0.6.0", **matched})
    _assert(ok.status == mod.Status.PASS, "matching key + same release -> PASS")
    behind = _hub_shared_current(
        {**kit, "DOCKER_TAG": "v0.5.0"}, {"version": "v0.5.0", "hub_version": "v0.6.0", **matched}
    )
    _assert(behind.status == mod.Status.WARN, "kit behind the hub -> WARN (not blocking)")
    _assert(any("upgrade-onprem-trust" in h for h in behind.hints), "hint names the upgrade verb")


def test_16_hub_shared_current_warns_until_trust_api_can_answer() -> None:
    """First install (trust-api not running) or a pre-FLIP#1204 trust-api -> WARN, never FAIL or PENDING:
    the upgrade verb runs this checklist as its gate, and is exactly what gives the site a trust-api that
    can answer — a PENDING here would make an old site un-upgradeable."""
    print("▶ hub-shared currency: trust-api unreachable / pre-FLIP#1204 -> WARN")
    kit = {"TRUST_API_PORT": "8020", "DOCKER_TAG": "v0.6.0"}
    down = _hub_shared_current(kit, ConnectionRefusedError("refused"))
    _assert(down.status == mod.Status.WARN, "unreachable trust-api -> WARN")
    old = _hub_shared_current(kit, {"version": "0.5.0"})
    _assert(old.status == mod.Status.WARN, "trust-api without hub_key_match -> WARN")
    unknown = _hub_shared_current(kit, {"version": "v0.6.0", "hub_version": None, "hub_key_match": None})
    _assert(unknown.status == mod.Status.WARN, "hub_key_match None (no heartbeat reply yet) -> WARN")


def test_17_hub_shared_current_judges_the_kit_not_the_running_key() -> None:
    """After a refreshed Hub-shared block, trust-api still runs the old key until the upgrade recreates
    it; the gate must let that upgrade through (FLIP#1204) and FAIL only a kit that is still stale."""
    print("▶ hub-shared currency: judged by the kit's key against the hub's fingerprint")
    _assert(mod.kit_key_fingerprint({"AES_KEY_BASE64": HUB_KEY_B64}) == HUB_KEY_FP, "kit digest = hub formula")
    _assert(mod.kit_key_fingerprint({"AES_KEY_BASE64": "not base64!"}) is None, "undecodable key -> None")
    refreshed = {"TRUST_API_PORT": "8020", "DOCKER_TAG": "v0.6.0", "AES_KEY_BASE64": HUB_KEY_B64}
    pending = _hub_shared_current(
        refreshed, {"hub_version": "v0.6.0", "hub_key_match": False, "hub_key_fingerprint": HUB_KEY_FP}
    )
    _assert(pending.status == mod.Status.WARN, "refreshed kit, old running key -> WARN", pending.detail)
    _assert("recreates" in pending.detail, "says the upgrade recreates trust-api", pending.detail)
    stale = {**refreshed, "AES_KEY_BASE64": STALE_KEY_B64}
    still_stale = _hub_shared_current(
        stale, {"hub_version": "v0.6.0", "hub_key_match": False, "hub_key_fingerprint": HUB_KEY_FP}
    )
    _assert(still_stale.status == mod.Status.FAIL, "kit key differs from the hub's -> FAIL")
    broken_by_edit = _hub_shared_current(
        stale, {"hub_version": "v0.6.0", "hub_key_match": True, "hub_key_fingerprint": HUB_KEY_FP}
    )
    _assert(broken_by_edit.status == mod.Status.FAIL, "running key fine but the kit now differs -> FAIL")
    ok = _hub_shared_current(
        refreshed, {"hub_version": "v0.6.0", "hub_key_match": True, "hub_key_fingerprint": HUB_KEY_FP}
    )
    _assert(ok.status == mod.Status.PASS, "kit and running key both match -> PASS", ok.detail)
    damaged = _hub_shared_current(
        {**refreshed, "AES_KEY_BASE64": "not base64!"},
        {"hub_version": "v0.6.0", "hub_key_match": True, "hub_key_fingerprint": HUB_KEY_FP},
    )
    _assert(damaged.status == mod.Status.FAIL, "undecodable kit key -> FAIL", damaged.detail)
    _assert("not a valid base64" in damaged.detail, "says the key is damaged, not stale", damaged.detail)


def test_18_a_gate_never_suggests_a_command() -> None:
    """Standalone, READY names `up-onprem-trust`; as a verb's gate it would name the wrong command (the
    upgrade: up-onprem-trust resets XNAT) or the one already running (the first install)."""
    print("▶ READY footer: standalone names up-onprem-trust, a gate does not")
    all_pass = [mod.Check("x", mod.Status.PASS, "")]
    for argv, expect_first_install in ((["prog", "Trust_9"], True), (["prog", "Trust_9", "--gate"], False)):
        out = io.StringIO()
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(sys, "stdout", out),
            mock.patch.object(mod, "fetch_public_ip", return_value="192.0.2.1"),
            mock.patch.object(mod, "run_checks", return_value=all_pass),
        ):
            try:
                mod.main()
            except SystemExit as e:
                _assert(e.code == 0, f"{argv[1:]} READY exits 0")
        text = out.getvalue()
        named = "up-onprem-trust" in text.split("Status: READY", 1)[1]
        _assert(named == expect_first_install, f"{argv[1:]}: footer names up-onprem-trust = {expect_first_install}")


def test_19_hub_on_its_release_sha_build_is_not_behind() -> None:
    """A CI-applied hub reports the sha build of its release commit; from that release's checkout the
    kit pinning the release is current, not "behind"."""
    print("▶ hub-shared currency: hub on sha-<release commit> vs a kit on that release -> PASS")
    kit = {"TRUST_API_PORT": "8020", "DOCKER_TAG": "v0.7.0", "AES_KEY_BASE64": HUB_KEY_B64}
    health = {"hub_version": "sha-23cf331", "hub_key_match": True, "hub_key_fingerprint": HUB_KEY_FP}
    with mock.patch.object(mod.site_upgrade, "release_for_sha", return_value="v0.7.0") as mapped:
        ok = _hub_shared_current(kit, health)
    _assert(ok.status == mod.Status.PASS, "sha of the kit's release -> PASS", ok.detail)
    _assert(mapped.call_args.args == ("sha-23cf331",), "asks the checkout about the hub's sha")
    with mock.patch.object(mod.site_upgrade, "release_for_sha", return_value=None):
        other = _hub_shared_current(kit, health)
    _assert(other.status == mod.Status.WARN, "an unrelated sha is still reported", other.detail)


def main() -> None:
    if not SCRIPT.is_file():
        sys.exit(f"❌ {SCRIPT} not found")

    test_1_unreadable_dir_warns_not_crashes()
    test_2_populated_dir_passes()
    test_3_empty_dir_warns()
    test_4_missing_dir_warns()
    test_5_site_privacy_uses_runtime_validator()
    test_6_site_privacy_rejects_non_finite_value()
    test_7_site_privacy_rejects_unsupported_backend()
    test_8_site_privacy_not_configured_reports_cleanly()
    test_9_site_privacy_rejects_misspelt_variable()
    test_10_gpu_unset_on_nvflare_warns_without_probing()
    test_11_gpu_unset_on_flower_is_cpu_only()
    test_12_gpu_explicit_zero_is_cpu_only_pass()
    test_13_gpu_explicit_one_unchanged()
    test_14_hub_shared_current_flags_a_stale_key()
    test_15_hub_shared_current_passes_and_notes_the_release_gap()
    test_16_hub_shared_current_warns_until_trust_api_can_answer()
    test_17_hub_shared_current_judges_the_kit_not_the_running_key()
    test_18_a_gate_never_suggests_a_command()
    test_19_hub_on_its_release_sha_build_is_not_behind()

    print("—")
    print(f"PASS={PASS}  FAIL={FAIL}")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
