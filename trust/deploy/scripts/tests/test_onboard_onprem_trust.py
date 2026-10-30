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
    uv run trust/deploy/scripts/tests/test_onboard_onprem_trust.py
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import shlex
import socket
import subprocess
import sys
import tempfile
import urllib.error
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
        "OMOP data dir",
        "OMOP_DATA_DIR",
        {"OMOP_DATA_DIR": str(path)},
        True,
        SCRIPTS_DIR.parents[2],
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
    """The readiness check accepts a valid FLARE percentile policy."""
    print("▶ valid FLARE site privacy policy -> PASS")
    result = mod.check_site_privacy_policy(
        {"FL_BACKEND": "nvflare", "FL_SITE_PRIVACY_POLICY": "percentile"},
        True,
        "TEST",
        SCRIPTS_DIR.parents[2],
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
        SCRIPTS_DIR.parents[2],
    )
    _assert(result.status == mod.Status.FAIL, "status is FAIL", result.detail)


def test_7_site_privacy_rejects_unsupported_backend() -> None:
    """A configured policy must not appear active when Flower will ignore it."""
    print("▶ site privacy policy on Flower -> FAIL")
    result = mod.check_site_privacy_policy(
        {"FL_BACKEND": "flower", "FL_SITE_PRIVACY_POLICY": "percentile"},
        True,
        "TEST",
        SCRIPTS_DIR.parents[2],
    )
    _assert(result.status == mod.Status.FAIL, "status is FAIL", result.detail)
    _assert("nothing on flower enforces it" in result.detail, "detail explains that Flower ignores the policy")


def test_8_site_privacy_not_configured_reports_cleanly() -> None:
    """A kit with no FL_SITE_PRIVACY_* vars passes without claiming a stale render exists."""
    print("▶ no site privacy policy configured -> PASS, clean detail")
    result = mod.check_site_privacy_policy(
        {"FL_BACKEND": "nvflare"},
        True,
        "TEST",
        SCRIPTS_DIR.parents[2],
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
        SCRIPTS_DIR.parents[2],
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


EXAMPLE_DOCUMENT = SCRIPTS_DIR.parents[2] / "trust" / "governance.example.toml"


def _document(text: str) -> str:
    handle = tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False, encoding="utf-8")
    handle.write(text)
    handle.close()
    return handle.name


def test_20_governance_document_valid_passes_with_its_digest() -> None:
    """The checklist that gates upgrade-onprem-trust validates the [disclosure]/[access] half."""
    print("▶ valid governance document -> PASS, names the digest")
    result = mod.check_governance_document(
        {"FL_BACKEND": "nvflare", "ACCESS_POLICY_FILE": str(EXAMPLE_DOCUMENT)}, True, "TEST", SCRIPTS_DIR.parents[2]
    )
    _assert(result.status == mod.Status.PASS, "status is PASS", result.detail)
    _assert("sha256 " in result.detail, "detail carries the digest the service logs", result.detail)


def test_21_governance_document_below_the_floor_fails() -> None:
    print("▶ governance document lowering the kit floor -> FAIL")
    document = _document("[disclosure]\nmin_cohort_size = 5\n")
    result = mod.check_governance_document(
        {"ACCESS_POLICY_FILE": document, "COHORT_QUERY_THRESHOLD": "10"}, True, "TEST", SCRIPTS_DIR.parents[2]
    )
    _assert(result.status == mod.Status.FAIL, "status is FAIL", result.detail)
    _assert("below the configured COHORT_QUERY_THRESHOLD" in result.detail, "detail is the loader's", result.detail)


def test_22_governance_document_not_configured_passes() -> None:
    print("▶ no governance document -> PASS, platform defaults")
    result = mod.check_governance_document({"COHORT_QUERY_THRESHOLD": "12"}, True, "TEST", SCRIPTS_DIR.parents[2])
    _assert(result.status == mod.Status.PASS, "status is PASS", result.detail)
    _assert("COHORT_QUERY_THRESHOLD=12" in result.detail, "detail names the floor in force", result.detail)


def test_23_site_privacy_reads_the_documents_section() -> None:
    """A document with gamma = 0 used to PASS "no site privacy policy configured" here, then crash-loop the client."""
    print("▶ fail-open gamma in the document -> FAIL")
    document = _document('[fl_privacy.nvflare]\npolicy = "percentile"\ngamma = 0\n')
    result = mod.check_site_privacy_policy(
        {"FL_BACKEND": "nvflare", "ACCESS_POLICY_FILE": document}, True, "TEST", SCRIPTS_DIR.parents[2]
    )
    _assert(result.status == mod.Status.FAIL, "status is FAIL", result.detail)
    _assert("gamma" in result.detail, "detail names the parameter", result.detail)


def test_24_site_privacy_in_both_sources_fails() -> None:
    print("▶ filter in both the document and the kit -> FAIL")
    result = mod.check_site_privacy_policy(
        {"FL_BACKEND": "nvflare", "ACCESS_POLICY_FILE": str(EXAMPLE_DOCUMENT), "FL_SITE_PRIVACY_POLICY": "percentile"},
        True,
        "TEST",
        SCRIPTS_DIR.parents[2],
    )
    _assert(result.status == mod.Status.FAIL, "status is FAIL", result.detail)
    _assert("configured twice" in result.detail, "detail says why", result.detail)


def test_25_documents_nvflare_section_on_flower_fails() -> None:
    print("▶ [fl_privacy.nvflare] on a Flower trust -> FAIL")
    result = mod.check_site_privacy_policy(
        {"FL_BACKEND": "flower", "ACCESS_POLICY_FILE": str(EXAMPLE_DOCUMENT)}, True, "TEST", SCRIPTS_DIR.parents[2]
    )
    _assert(result.status == mod.Status.FAIL, "status is FAIL", result.detail)


def test_26_a_relative_document_resolves_against_trust() -> None:
    """Compose resolves ACCESS_POLICY_FILE against trust/ (--project-directory trust); so does the checklist."""
    print("▶ relative ACCESS_POLICY_FILE -> resolved against trust/")
    result = mod.check_governance_document(
        {"ACCESS_POLICY_FILE": "./governance.example.toml"}, True, "TEST", SCRIPTS_DIR.parents[2]
    )
    _assert(result.status == mod.Status.PASS, "status is PASS", result.detail)


def test_27_selected_kit_controls_values_and_filename_hints() -> None:
    """Read the suffixed kit, even when an unsuffixed kit with different values exists."""
    print("▶ selected kit controls values and filename hints")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        trust = root / "trust"
        trust.mkdir()
        (trust / ".env.SITE").write_text("EXPECTED_TRUST_ID=wrong-file\n")
        selected = trust / ".env.SITE.prod"
        selected.write_text(
            "EXPECTED_TRUST_ID=selected-file\nNUM_AVAILABLE_GPUS=invalid\n"  # pragma: allowlist secret (synthetic kit)
        )
        passed = mod.Check("external probe", mod.Status.PASS, "mocked")
        with (
            mock.patch.object(mod, "check_swarm", return_value=passed),
            mock.patch.object(mod, "check_hub_shared_current", return_value=passed),
            mock.patch.object(mod, "check_registries_reachable", return_value=passed),
            mock.patch.object(mod.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "invalid")),
        ):
            checks = mod.run_checks("SITE", root, selected)
        present = next(check for check in checks if check.label == "Kit file present")
        _assert(present.status == mod.Status.PASS, "selected file is present")
        _assert(present.detail == "trust/.env.SITE.prod", "names the selected file", present.detail)
        identity = next(check for check in checks if check.label == "EXPECTED_TRUST_ID self-check")
        _assert(identity.detail == "set to selected-file", "reads the selected kit's values", identity.detail)
        hints = "\n".join(hint for check in checks for hint in check.hints)
        _assert("trust/.env.SITE.prod" in hints, "edit hints name the selected kit", hints)
        _assert("trust/.env.SITE " not in hints and "trust/.env.SITE," not in hints, "no legacy-path advice", hints)
        missing = mod.check_kit_file("SITE", selected.with_name("missing.prod"))
        _assert(missing.status == mod.Status.FAIL, "missing explicit file still fails readiness")


def test_28_missing_file_heading_and_advice_name_the_selected_path() -> None:
    """Missing suffixed kits give actionable diagnostics without silently checking a legacy kit."""
    print("▶ selected missing kit is named in the heading and advice")
    selected = SCRIPTS_DIR.parents[2] / "trust/.env.MISSING.prod"
    out = io.StringIO()
    exit_code = None
    with (
        mock.patch.object(sys, "argv", ["prog", "MISSING", "--kit-file", str(selected), "--gate"]),
        mock.patch.object(sys, "stdout", out),
        mock.patch.object(mod, "check_swarm", return_value=mod.Check("swarm", mod.Status.PASS, "mocked")),
    ):
        try:
            mod.main()
        except SystemExit as error:
            exit_code = error.code
    _assert(exit_code == 1, "missing selected kit exits 1")
    _assert(out.getvalue().count("trust/.env.MISSING.prod") >= 3, "heading and hints name selected path")
    _assert("Status: NOT READY" in out.getvalue(), "reports NOT READY")


def test_29_ready_command_preserves_and_quotes_the_selected_kit() -> None:
    """Copying the READY command must deploy the kit that was checked, including custom filenames."""
    print("▶ READY command preserves and shell-quotes the selected kit")
    selected_paths = (
        SCRIPTS_DIR.parents[2] / "trust/.env.SITE.stag",
        Path("/opt/operator kits/kit's file; echo unsafe"),
        Path('/opt/operator kits/$HOME `echo unsafe` "file"'),
    )
    for selected in selected_paths:
        for hub, sudo_user in ((None, None), ("prod", None), ("stag", None), ("prod", "operator")):
            env = {k: v for k, v in (("HUB", hub), ("SUDO_USER", sudo_user)) if v is not None}
            out = io.StringIO()
            exit_code = None
            with (
                mock.patch.dict(os.environ, env, clear=True),
                mock.patch.object(sys, "argv", ["prog", "SITE", "--kit-file", str(selected)]),
                mock.patch.object(sys, "stdout", out),
                mock.patch.object(mod, "run_checks", return_value=[mod.Check("x", mod.Status.PASS, "mocked")]),
            ):
                try:
                    mod.main()
                except SystemExit as error:
                    exit_code = error.code
            text = out.getvalue().replace(mod.BOLD, "").replace(mod.RESET, "")
            command = next(line.strip() for line in text.splitlines() if "make up-onprem-trust" in line)
            _assert(exit_code == 0, f"{selected.name}: READY exits 0")
            # Docker socket access is the requirement; sudo appears only when the checklist ran under it.
            expected = ["sudo", "-E"] if sudo_user else []
            expected += ["make", "up-onprem-trust", "KIT=SITE", f"KIT_FILE={selected}".replace("$", "$$")]
            if hub and hub != "prod":
                expected.append(f"HUB={hub}")
            _assert(
                shlex.split(command) == expected,
                f"{selected.name}: selected path remains one argument and HUB={hub} is preserved",
                command,
            )


def test_30_governance_action_preserves_and_quotes_the_selected_kit() -> None:
    """Detailed governance checks must read the same kit as the failed readiness check."""
    print("▶ failed governance advice preserves and shell-quotes the selected kit")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        selected = root / 'trust/kit\'s $HOME `echo unsafe` "file".prod'
        selected.parent.mkdir()
        selected.write_text("ACCESS_POLICY_FILE=./governance.toml\n")
        passed = mod.Check("external probe", mod.Status.PASS, "mocked")
        for hub in (None, "prod", "stag"):
            with (
                mock.patch.dict(os.environ, {} if hub is None else {"HUB": hub}, clear=True),
                mock.patch.object(mod, "check_swarm", return_value=passed),
                mock.patch.object(mod, "check_hub_shared_current", return_value=passed),
                mock.patch.object(mod, "check_registries_reachable", return_value=passed),
                mock.patch.object(
                    mod.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "❌ bad policy\n", "")
                ),
            ):
                checks = mod.run_checks("SITE", root, selected)
            result = next(check for check in checks if check.label == "Governance document")
            _assert(result.status == mod.Status.FAIL, "governance validation still fails")
            hint = next(hint for hint in result.hints if "check-governance" in hint)
            command = hint[1 : hint.rfind("`")]
            expected = [
                "make",
                "-C",
                "trust",
                "check-governance",
                "KIT=SITE",
                f"KIT_FILE={selected}".replace("$", "$$"),
            ]
            if hub:
                expected.append(f"HUB={hub}")
            _assert(
                shlex.split(command) == expected,
                "detailed-check advice keeps the selected path as one argument and preserves HUB",
                command,
            )


def test_31_unrotated_passwords_compare_against_the_kit_template() -> None:
    """Every kit is scaffolded from trust/.env.example, so that is the template its defaults come from."""
    print("\n[31] Trust-local passwords: compared against trust/.env.example")
    repo_root = SCRIPTS_DIR.parents[2]
    defaults = mod.read_kit_vars(repo_root / "trust" / ".env.example")
    rotated = {key: "rotated-secret" for key in mod.TRUST_LOCAL_PASSWORD_KEYS}  # pragma: allowlist secret
    for kit_vars, root, status in (
        ({"ORTHANC_PASSWORD": defaults["ORTHANC_PASSWORD"]}, repo_root, mod.Status.WARN),
        (rotated, repo_root, mod.Status.PASS),
        (rotated, Path(tempfile.mkdtemp()), mod.Status.WARN),  # no template: say so, never PASS
    ):
        check = mod.check_unrotated_passwords(kit_vars, True, root, "SITE")
        _assert(check.status == status, f"{status.name}: {check.detail}", str(check))


def test_32_hub_reachable_reports_the_release_or_the_failure() -> None:
    """GET <hub>/health from this host: PASS names the release; FAIL names why, in firewall-request words."""
    print("\n[32] Hub API reachable: release on PASS, reason on FAIL, PENDING without the URL")
    kit = {"CENTRAL_HUB_API_URL": "https://hub.example/api"}
    with mock.patch.object(mod.site_upgrade, "fetch_hub_version", return_value="v0.14.0"):
        ok = mod.check_hub_reachable(kit, True)
    _assert(ok.status == mod.Status.PASS, "answers -> PASS", str(ok))
    _assert("v0.14.0" in ok.detail, "PASS names the hub's release", ok.detail)
    failures = (
        (urllib.error.URLError(socket.gaierror(8, "nodename nor servname provided")), "DNS lookup failed"),
        (urllib.error.URLError(TimeoutError()), "timed out"),
        (urllib.error.URLError(ConnectionRefusedError()), "connection refused"),
        (urllib.error.HTTPError("https://hub.example/api/health", 503, "unavailable", {}, None), "HTTP 503"),
        (json.JSONDecodeError("Expecting value", "<html>", 0), "not with the health JSON"),  # UI page: URL lacks /api
    )
    for error, reason in failures:
        with mock.patch.object(mod.site_upgrade, "fetch_hub_version", side_effect=error):
            bad = mod.check_hub_reachable(kit, True)
        _assert(bad.status == mod.Status.FAIL, f"{reason}: FAIL", str(bad))
        _assert(reason in bad.detail, f"detail names the reason: {reason}", bad.detail)
        _assert("hub.example" in bad.detail, "detail names the host", bad.detail)
        _assert(any("Network requirements" in h for h in bad.hints), "hint points at the egress list")
    pending = mod.check_hub_reachable({}, True)
    _assert(pending.status == mod.Status.PENDING, "no CENTRAL_HUB_API_URL -> PENDING", str(pending))
    _assert(mod.check_hub_reachable(kit, False).status == mod.Status.PENDING, "no kit file -> PENDING")


def test_33_fl_endpoint_comes_from_what_the_client_dials() -> None:
    """FLARE: the kit slice's fed_client.json target when on disk, else the kit's NLB_SUBDOMAIN;
    Flower: the kit's NLB_SUBDOMAIN with FL_EDGE_PORT over FL_SERVER_PORT. Probed by a plain TCP connect."""
    print("\n[33] FL endpoint reachable: source per backend, TCP probe, TLS-inspection hint on FAIL")
    with tempfile.TemporaryDirectory() as tmp:
        kit_dir = Path(tmp)
        startup = kit_dir / "net-1" / "services" / "Trust_5" / "startup"
        startup.mkdir(parents=True)
        (startup / "fed_client.json").write_text(
            json.dumps({"servers": [{"service": {"target": "fl.hub.example:8002"}}]})
        )
        flare = {
            "FL_BACKEND": "nvflare",
            "FL_KIT_DIR": str(kit_dir),
            "FL_KIT_SLOT": "Trust_5",
            "NLB_SUBDOMAIN": "kit.hub.example",
            "FL_SERVER_PORT": "8002",
            "FL_EDGE_PORT": "8012",
        }
        from_slice = mod.fl_endpoint(flare)
        _assert(
            from_slice == ("fl.hub.example", 8002, "startup/fed_client.json"),
            "FLARE with the slice on disk dials fed_client.json's target",
            str(from_slice),
        )
        no_slice = mod.fl_endpoint({**flare, "FL_KIT_SLOT": "Trust_6"})
        _assert(
            no_slice == ("kit.hub.example", 8012, "the kit's NLB_SUBDOMAIN"),
            "FLARE without the slice falls back to the kit",
            str(no_slice),
        )
        flower = {**flare, "FL_BACKEND": "flower"}
        _assert(
            mod.fl_endpoint(flower) == ("kit.hub.example", 8012, "the kit's NLB_SUBDOMAIN"), "Flower: kit, edge port"
        )
        del flower["FL_EDGE_PORT"]
        _assert(mod.fl_endpoint(flower)[1] == 8002, "Flower pre-FLIP#1365 kit: FL_SERVER_PORT")
        _assert(mod.fl_endpoint({"FL_BACKEND": "flower"}) is None, "no host yet -> None")

        with mock.patch.object(mod, "probe_tcp", return_value=None) as probe:
            ok = mod.check_fl_endpoint_reachable(flare, True)
        _assert(ok.status == mod.Status.PASS, "connects -> PASS", str(ok))
        _assert(probe.call_args.args == ("fl.hub.example", 8002), "probed the dialled host:port", str(probe.call_args))
        with mock.patch.object(mod, "probe_tcp", return_value="timed out (blackholed or filtered)"):
            bad = mod.check_fl_endpoint_reachable(flare, True)
        _assert(bad.status == mod.Status.FAIL, "cannot connect -> FAIL", str(bad))
        _assert("fl.hub.example:8002" in bad.detail, "FAIL names host:port", bad.detail)
        _assert(any("TLS-inspection" in h for h in bad.hints), "hint covers the TLS-inspection proxy case")
        pending = mod.check_fl_endpoint_reachable({"FL_BACKEND": "flower"}, True)
        _assert(pending.status == mod.Status.PENDING, "no endpoint yet -> PENDING", str(pending))


def test_34_registries_warn_and_name_the_mirror() -> None:
    """Both hosts of each registry on 443; unreachable ones WARN (an offline mirror is supported, FLIP#1721)."""
    print("\n[34] Image registries reachable: PASS, else WARN naming the hosts and the mirror")
    with mock.patch.object(mod, "probe_tcp", return_value=None):
        ok = mod.check_registries_reachable()
    _assert(ok.status == mod.Status.PASS, "all connect -> PASS", str(ok))

    def layers_blocked(host: str, port: int, timeout: float = 0) -> str | None:
        return "DNS lookup failed" if host == "pkg-containers.githubusercontent.com" else None

    with mock.patch.object(mod, "probe_tcp", side_effect=layers_blocked):
        warn = mod.check_registries_reachable()
    _assert(warn.status == mod.Status.WARN, "a blocked host -> WARN, never FAIL", str(warn))
    _assert(
        "pkg-containers.githubusercontent.com: DNS lookup failed" in warn.detail, "names host and reason", warn.detail
    )
    _assert(any("FLIP#1721" in h for h in warn.hints), "hint names the mirror alternative")


def test_35_the_checklist_carries_the_three_rows_and_gates_on_the_hub() -> None:
    """run_checks includes the rows; an unreachable hub is a FAIL, so --gate refuses to start."""
    print("\n[35] run_checks: the reachability rows are present; hub FAIL makes the checklist NOT READY")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "trust").mkdir()
        kit_file = root / "trust" / ".env.SITE.prod"
        kit_file.write_text("CENTRAL_HUB_API_URL=https://hub.example/api\n")
        passed = mod.Check("external probe", mod.Status.PASS, "mocked")
        with (
            mock.patch.object(mod, "check_swarm", return_value=passed),
            mock.patch.object(mod, "check_hub_shared_current", return_value=passed),
            mock.patch.object(mod, "probe_hub_health", return_value=(None, "timed out (blackholed or filtered)")),
            mock.patch.object(mod, "probe_tcp", return_value=None),
            mock.patch.object(mod.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")),
        ):
            checks = mod.run_checks("SITE", root, kit_file)
    labels = [check.label for check in checks]
    for label in ("Hub API reachable", "FL endpoint reachable", "Image registries reachable"):
        _assert(label in labels, f"row present: {label}")
    hub = next(check for check in checks if check.label == "Hub API reachable")
    _assert(hub.status == mod.Status.FAIL, "unreachable hub is a FAIL (blocks --gate)", str(hub))
    registries = next(check for check in checks if check.label == "Image registries reachable")
    _assert(registries.status == mod.Status.PASS, "registries probed through probe_tcp", str(registries))


def _paths(**kit: str):
    return mod.check_data_dir_paths(kit, True, "trust/.env.SITE.prod")


def test_36_relative_data_dirs_warn() -> None:
    """The kit template's development paths resolve inside the checkout on a deployed trust (FLIP#1723)."""
    print("▶ relative data dir paths -> WARN naming them")
    with tempfile.TemporaryDirectory() as td:
        check = _paths(
            OMOP_DATA_DIR=f"{td}/omop",
            ORTHANC_STORAGE_DIR="./orthanc/orthanc-storage-trust1",
            BASE_IMAGES_DOWNLOAD_DIR="./data/trust",
            COHORT_SNAPSHOT_STORAGE_DIR=f"{td}/snapshots",
        )
    _assert(check.status == mod.Status.WARN, "WARN", f"{check.status}: {check.detail}")
    _assert("ORTHANC_STORAGE_DIR, BASE_IMAGES_DOWNLOAD_DIR" in check.detail, "names both", check.detail)
    _assert("trust/.env.SITE.prod" in check.hints[0], "hint names the kit", check.hints[0])


def test_37_uncreatable_data_dir_fails() -> None:
    """A dir the bring-up's mkdir cannot create fails here, not halfway through `up` (FLIP#1723)."""
    print("▶ data dir under an unwritable parent -> FAIL; the container-made store is exempt")
    with tempfile.TemporaryDirectory() as td:
        locked = Path(td) / "locked"
        locked.mkdir()
        writable = lambda path, mode: Path(path) != locked  # noqa: E731 — root ignores mode bits, so fake it
        with mock.patch.object(mod.os, "access", side_effect=writable):
            check = _paths(
                OMOP_DATA_DIR=f"{locked}/omop",
                ORTHANC_STORAGE_DIR=f"{td}/orthanc",
                BASE_IMAGES_DOWNLOAD_DIR=f"{td}/images",
                COHORT_SNAPSHOT_STORAGE_DIR=f"{locked}/snapshots",
            )
    _assert(check.status == mod.Status.FAIL, "FAIL", f"{check.status}: {check.detail}")
    _assert(f"OMOP_DATA_DIR ({locked} is not writable)" in check.detail, "names the var and parent", check.detail)
    _assert("COHORT_SNAPSHOT_STORAGE_DIR" not in check.detail, "membership store exempt", check.detail)


def test_38_absolute_creatable_data_dirs_pass() -> None:
    print("▶ absolute, creatable data dirs -> PASS")
    with tempfile.TemporaryDirectory() as td:
        check = _paths(**{var: f"{td}/{var.lower()}" for var in mod.DATA_DIR_VARS})
    _assert(check.status == mod.Status.PASS, "PASS", f"{check.status}: {check.detail}")


def test_39_trust_health_probe_ignores_the_proxy() -> None:
    """Behind a TRE proxy with NO_PROXY unset, urllib would send 127.0.0.1 to the proxy (FLIP#1723)."""
    print("▶ local trust-api probe bypasses http_proxy")
    import http.server
    import threading

    class Health(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 — http.server's name
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status": "ok"}')

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with mock.patch.dict(os.environ, {"http_proxy": "http://127.0.0.1:9", "no_proxy": ""}):
            try:
                body = mod.fetch_local_trust_health(str(server.server_address[1]))
            except OSError as error:
                body = {"error": str(error)}
    finally:
        server.shutdown()
    _assert(body == {"status": "ok"}, "answers directly", str(body))


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
    test_20_governance_document_valid_passes_with_its_digest()
    test_21_governance_document_below_the_floor_fails()
    test_22_governance_document_not_configured_passes()
    test_23_site_privacy_reads_the_documents_section()
    test_24_site_privacy_in_both_sources_fails()
    test_25_documents_nvflare_section_on_flower_fails()
    test_26_a_relative_document_resolves_against_trust()
    test_27_selected_kit_controls_values_and_filename_hints()
    test_28_missing_file_heading_and_advice_name_the_selected_path()
    test_29_ready_command_preserves_and_quotes_the_selected_kit()
    test_30_governance_action_preserves_and_quotes_the_selected_kit()
    test_31_unrotated_passwords_compare_against_the_kit_template()
    test_32_hub_reachable_reports_the_release_or_the_failure()
    test_33_fl_endpoint_comes_from_what_the_client_dials()
    test_34_registries_warn_and_name_the_mirror()
    test_35_the_checklist_carries_the_three_rows_and_gates_on_the_hub()
    test_36_relative_data_dirs_warn()
    test_37_uncreatable_data_dir_fails()
    test_38_absolute_creatable_data_dirs_pass()
    test_39_trust_health_probe_ignores_the_proxy()

    print("—")
    print(f"PASS={PASS}  FAIL={FAIL}")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
