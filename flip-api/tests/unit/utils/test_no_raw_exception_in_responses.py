# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

"""Regression guard: HTTP responses must not carry raw exception text.

``str(e)`` on an unexpected exception is written by the failing library, not by us.
SQLAlchemy stringifies the failing SQL with table and constraint names, boto surfaces
ARNs and operation detail, and httpx leaks internal hostnames — all of it useful
reconnaissance for an authenticated caller. Handlers must return a fixed message and
let the correlation id tie the response back to the logged traceback.

This scans source rather than exercising endpoints because the risk is the *pattern*:
one new ``detail=str(e)`` reintroduces the leak on a path no test covers.
"""

import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[3] / "src" / "flip_api"

# `detail=str(e)`, `detail=f"...{e}"`, `detail=f"...{str(e)}"` and the same for the
# `message=` / `"error":` fields that also reach a client or the hub.
LEAK_PATTERN = re.compile(
    r"""(?:detail|message)\s*=\s*(?:str\(\s*e\s*\)|f["'][^"']*\{(?:str\(\s*e\s*\)|e)\})"""
    r"""|["']error["']\s*:\s*str\(\s*e\s*\)"""
    # The indirect form: a message built with the exception, then passed as `detail`.
    r"""|\berror_message\s*=\s*f["'][^"']*\{(?:str\(\s*e\s*\)|e)\}""",
)


def _python_sources() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def test_source_tree_is_present():
    """Guard the guard: a wrong path would make every assertion below vacuous."""
    assert SRC.is_dir(), f"expected flip_api source at {SRC}"
    assert _python_sources(), "no python sources found to scan"


def test_no_raw_exception_text_in_http_responses():
    offenders: list[str] = []
    for path in _python_sources():
        lines = path.read_text().splitlines()
        for lineno, line in enumerate(lines, start=1):
            # Opt-out for exceptions we define ourselves, whose message is written for
            # the user (e.g. "A trust named X already exists"). Must be justified inline.
            if "safe-detail:" in line or (lineno > 1 and "safe-detail:" in lines[lineno - 2]):
                continue
            # Logging the exception server-side is the desired behaviour, not a leak.
            if "logger." in line:
                continue
            if LEAK_PATTERN.search(line):
                offenders.append(f"{path.relative_to(SRC)}:{lineno}: {line.strip()}")

    assert not offenders, (
        "Raw exception text must not be returned to callers — use a fixed message and "
        "let the correlation id link it to the logged traceback:\n  " + "\n  ".join(offenders)
    )


@pytest.mark.parametrize(
    "snippet",
    [
        'raise HTTPException(status_code=500, detail=str(e))',
        'raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")',
        'raise HTTPException(status_code=502, detail=f"Upstream failed: {e}")',
        'return {"success": False, "error": str(e)}',
    ],
)
def test_pattern_catches_known_leak_shapes(snippet):
    """The guard is only worth having if it actually matches the shapes it bans."""
    assert LEAK_PATTERN.search(snippet)


@pytest.mark.parametrize(
    "snippet",
    [
        'raise HTTPException(status_code=500, detail="Internal server error")',
        'raise HTTPException(status_code=400, detail="Invalid SQL query")',
        'logger.exception(f"Error processing task: {e}")',  # logging detail is fine
        'detail=f"User with ID: {user_id} is not allowed to modify this project"',
    ],
)
def test_pattern_does_not_flag_safe_shapes(snippet):
    """Fixed messages, and logging the exception server-side, must stay allowed."""
    assert not LEAK_PATTERN.search(snippet)
