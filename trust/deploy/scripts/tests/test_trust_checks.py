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
"""Unit tests for trust/deploy/scripts/trust_checks.py — the one result type and report.

Usage:
    uv run --no-config trust/deploy/scripts/tests/test_trust_checks.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import trust_checks as tc  # noqa: E402
from trust_checks import Check, Status  # noqa: E402

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


def _report(checks: list[Check]) -> tuple[dict, str, str]:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "reports"
        path = tc.write_report(
            checks,
            out,
            stem="selftest-nvflare-20261125T100000Z",
            latest="latest-nvflare",
            title="Node self-test: nvflare",
            started="2026-11-25T10:00:00Z",
            finished="2026-11-25T10:05:00Z",
            backend="nvflare",
        )
        return (
            json.loads(path.with_suffix(".json").read_text()),
            path.read_text(),
            (out / "latest-nvflare.md").read_text(),
        )


def test_warnings_and_skips_do_not_fail_a_run() -> None:
    checks = [Check("a", Status.PASS), Check("b", Status.WARN, "open"), Check("c", Status.SKIPPED, "no data")]
    _assert(tc.exit_code(checks) == 0, "pass + warn + skipped exits 0")
    _assert(all(c.ok for c in checks), "each of them is ok")


def test_a_failure_or_a_pending_check_exits_1() -> None:
    _assert(tc.exit_code([Check("a", Status.PASS), Check("b", Status.FAIL)]) == 1, "a failure exits 1")
    _assert(tc.exit_code([Check("a", Status.PENDING)]) == 1, "a pending check exits 1")
    _assert(tc.exit_code([]) == 1, "a run with no checks is not a pass")


def test_a_check_that_could_not_run_is_neither_pass_nor_fail() -> None:
    def broken() -> Check:
        raise TimeoutError("kubectl did not answer")

    check = tc.run("release ready", broken)
    _assert(check.status is Status.ERROR and not check.ok, "a check that raises is ERROR")
    _assert("TimeoutError: kubectl did not answer" in check.detail, "its detail names what went wrong", check.detail)
    _assert(tc.exit_code([Check("a", Status.PASS), check]) == 2, "with nothing failed, it exits 2")
    _assert(tc.exit_code([Check("a", Status.FAIL), check]) == 1, "a real failure still exits 1")
    _assert(
        tc.run("x", lambda: Check("x", Status.PASS)).status is Status.PASS, "a check that returns is passed through"
    )


def test_report_json_shape_is_pinned() -> None:
    checks = [
        Check("hub reachable", Status.PASS, "200"),
        Check(
            "undeclared destinations blocked", Status.WARN, "example.com:443 answered", ("ask IT",), ("SATRE 2.2.11",)
        ),
    ]
    report, _, _ = _report(checks)
    _assert(list(report) == ["backend", "started", "finished", "ok", "checks"], "top-level keys", str(list(report)))
    _assert(report["ok"] is True, "a run with only a warning is ok")
    _assert(
        report["checks"][0] == {"name": "hub reachable", "ok": True, "status": "pass", "detail": "200"},
        "a plain row keeps name, ok and detail, and gains status",
        str(report["checks"][0]),
    )
    _assert(
        report["checks"][1]["hints"] == ["ask IT"] and report["checks"][1]["refs"] == ["SATRE 2.2.11"],
        "hints and refs appear only on rows that have them",
    )


def test_markdown_shows_each_status_and_the_headline() -> None:
    _, md, latest = _report(
        [Check("a | b", Status.PASS, "line one\nline two"), Check("c", Status.ERROR, "could not run")]
    )
    _assert("COULD NOT RUN · 2026-11-25T10:00:00Z → 2026-11-25T10:05:00Z" in md, "headline says the run could not run")
    _assert("| ✅ | a \\| b | line one line two |\n" in md, "a cell escapes pipes and folds line breaks", md)
    _assert("| 💥 | c | could not run |\n" in md, "the could-not-run row has its own glyph")
    _assert(latest == md, "latest-<name>.md is the same report")
    _, cited, _ = _report([Check("a", Status.PASS), Check("b", Status.WARN, "open", refs=("SATRE 2.2.11",))])
    _assert("| | Check | Detail | Tests |" in cited, "a report with a cited row gains the Tests column")
    _assert("| ✅ | a |  |  |" in cited and "| ⚠️ | b | open | SATRE 2.2.11 |" in cited, "every row fills it", cited)
    _, failed, _ = _report([Check("a", Status.FAIL)])
    _assert("FAILED ·" in failed, "a failure is headlined FAILED")


def main() -> None:
    test_warnings_and_skips_do_not_fail_a_run()
    test_a_failure_or_a_pending_check_exits_1()
    test_a_check_that_could_not_run_is_neither_pass_nor_fail()
    test_report_json_shape_is_pinned()
    test_markdown_shows_each_status_and_the_headline()
    print("—")
    print(f"PASS={PASS}  FAIL={FAIL}")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
