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
#
"""
Negative parser tests - confirm anon_engine.parse_script rejects every
.das construct it does not support.

The "fail loud" contract is the whole point of the test double: if a future
edit to anon_script.das introduces unfamiliar syntax, CI must surface it
rather than silently parse zero rules and pass. These tests exercise each
error branch in ``_parse_line`` against a synthetic .das file written into
``tmp_path``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from anon_engine import UnsupportedRuleError, parse_script


def _write_script(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "bad.das"
    path.write_text(body)
    return path


@pytest.mark.parametrize(
    ("body", "match"),
    [
        ("someDirective foo\n", r"expected '\(gggg,eeee\)"),
        ('(0010,0010) "ANON"\n', r"expected ':=' after tag"),
        ("- not_a_tag\n", r"cannot parse remove rule"),
        ("(0010,0010) := patient\n", r"unsupported RHS expression"),
        ("(0010,0010) := foo()\n", r"unsupported RHS expression"),
        ('(0010,0010) := "ANON" trailing\n', r"unsupported RHS expression"),
        ("(0010,0010) := hashUID[(not,a,tag)]\n", r"unsupported RHS expression"),
    ],
    ids=[
        "unknown_top_level_directive",
        "missing_assignment_operator",
        "malformed_remove_rule",
        "unknown_variable_name",
        "unsupported_rhs_function_call",
        "literal_with_trailing_garbage",
        "malformed_hashuid_argument",
    ],
)
def test_parse_script_rejects_unsupported_syntax(tmp_path: Path, body: str, match: str) -> None:
    """Each unsupported construct must raise UnsupportedRuleError, not parse silently."""
    path = _write_script(tmp_path, body)
    with pytest.raises(UnsupportedRuleError, match=match):
        parse_script(path)


def test_parse_script_reports_offending_line_number(tmp_path: Path) -> None:
    """Errors must reference the source line so PR debugging stays cheap."""
    path = _write_script(
        tmp_path,
        'version "6.5"\n'
        "// blank-ish header\n"
        "(0010,0020) := subject\n"
        "garbage on line four\n",
    )
    with pytest.raises(UnsupportedRuleError, match=r"line 4:"):
        parse_script(path)


def test_parse_script_ignores_comments_and_blank_lines(tmp_path: Path) -> None:
    """A file with only comments, blanks, and a version directive parses to no rules.

    This is the inverse of the negative cases: it pins down what the parser
    is *allowed* to skip, so a future change that loosens the grammar (e.g.
    accepting unknown directives as no-ops) breaks this test loudly.
    """
    path = _write_script(
        tmp_path,
        'version "6.5"\n'
        "\n"
        "// just a comment\n"
        "   // indented comment\n",
    )
    assert parse_script(path) == []
