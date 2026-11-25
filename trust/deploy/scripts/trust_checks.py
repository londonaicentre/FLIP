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
"""One result type and one report for every check of a trust node (FLIP#2075, stdlib only).

The onboarding checklist and the self-tests each say what they found with a :class:`Check`, and
each writes the same JSON and markdown report with :func:`write_report`. What differs between
them is only how they reach the node.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class Status(Enum):
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"  # worth the operator's attention; never fails a run
    SKIPPED = "skipped"  # nothing to check here, and the detail says why
    PENDING = "pending"  # waits on an earlier check, typically the kit file
    ERROR = "error"  # the check could not run, so it says nothing about the node

    @property
    def glyph(self) -> str:
        return _GLYPHS[self]


_GLYPHS = {
    Status.PASS: "✅",
    Status.FAIL: "❌",
    Status.WARN: "⚠️",
    Status.SKIPPED: "⏭️",
    Status.PENDING: "⏳",
    Status.ERROR: "💥",
}
# A run is ready when nothing failed, is still pending, or could not run.
_BLOCKING = (Status.FAIL, Status.PENDING, Status.ERROR)


@dataclass(frozen=True)
class Check:
    """One row of a report."""

    name: str
    status: Status
    detail: str = ""
    hints: Sequence[str] = ()  # what the operator can do about it
    refs: Sequence[str] = ()  # the requirements the check tests, e.g. "SATRE 2.2.11"

    @property
    def ok(self) -> bool:
        return self.status not in _BLOCKING


def passed(name: str, detail: str = "") -> Check:
    return Check(name, Status.PASS, detail)


def failed(name: str, detail: str = "") -> Check:
    return Check(name, Status.FAIL, detail)


def verdict(name: str, ok: bool, detail: str = "") -> Check:
    """Pass or fail on a condition, for a check whose detail reads the same either way."""
    return Check(name, Status.PASS if ok else Status.FAIL, detail)


def skipped(name: str, reason: str) -> Check:
    return Check(name, Status.SKIPPED, reason)


def run(name: str, check: Callable[[], Check]) -> Check:
    """Run one check; one that raises could not run, which is neither a pass nor a fail."""
    try:
        return check()
    except Exception as error:  # noqa: BLE001 — any failure of the check itself is the finding
        return could_not_run(name, error)


def could_not_run(name: str, error: Exception) -> Check:
    return Check(name, Status.ERROR, f"could not run: {type(error).__name__}: {error}")


def exit_code(checks: list[Check]) -> int:
    """0 ready; 1 a check failed or is pending; 2 nothing failed but a check could not run."""
    statuses = {c.status for c in checks}
    if not checks or statuses & {Status.FAIL, Status.PENDING}:
        return 1
    return 2 if Status.ERROR in statuses else 0


def _cell(text: str) -> str:
    """Text safe inside one markdown table cell: pipes escaped, line breaks folded to spaces."""
    return " ".join(text.split()).replace("|", "\\|")


def _headline(checks: list[Check]) -> str:
    return {0: "PASSED", 1: "FAILED", 2: "COULD NOT RUN"}[exit_code(checks)]


def as_dict(check: Check) -> dict:
    row: dict = {"name": check.name, "ok": check.ok, "status": check.status.value, "detail": check.detail}
    if check.hints:
        row["hints"] = list(check.hints)
    if check.refs:
        row["refs"] = list(check.refs)
    return row


def write_report(
    checks: list[Check], out: Path, *, stem: str, latest: str, title: str, started: str, finished: str, **meta: str
) -> Path:
    """Write ``<stem>.json``, ``<stem>.md`` and ``<latest>.md`` under ``out``; return the markdown path.

    ``meta`` holds what identifies the run (``backend``, ``shape``, ``kit``) and goes first in the JSON.
    """
    report = {
        **meta,
        "started": started,
        "finished": finished,
        "ok": exit_code(checks) == 0,
        "checks": [as_dict(c) for c in checks],
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{stem}.json").write_text(json.dumps(report, indent=2) + "\n")
    rows = "\n".join(
        f"| {c.status.glyph} | {_cell(c.name)} | {_cell(c.detail)} | {_cell(', '.join(c.refs))} |" for c in checks
    )
    md = (
        f"# {title}\n\n{_headline(checks)} · {started} → {finished}\n\n"
        f"| | Check | Detail | Tests |\n|---|---|---|---|\n{rows}\n"
    )
    path = out / f"{stem}.md"
    path.write_text(md)
    (out / f"{latest}.md").write_text(md)
    return path
