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

"""``seg`` behaviour, plus a source scan that keeps every XNAT URL using it.

The scan is the part that matters. Encoding one call site is easy; the finding was that
21 of them were built by raw f-string interpolation and one — ``get_command_info`` —
was not, which is exactly the kind of inconsistency nobody notices in review. This walks
the AST and fails if any value interpolated into a URL built from ``XNAT_URL`` reaches it
without going through ``seg``.
"""

import ast
from pathlib import Path

import pytest

from imaging_api.utils.xnat_url import seg

SRC = Path(__file__).resolve().parents[2] / "imaging_api"

#: The only names allowed to appear un-encoded inside an XNAT URL f-string. ``XNAT_URL``
#: is the base URL from settings, not a caller-supplied segment.
ALLOWED_BARE_NAMES = {"XNAT_URL"}


def _python_sources() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def _is_xnat_url_fstring(node: ast.JoinedStr) -> bool:
    """True if this f-string interpolates ``XNAT_URL`` — i.e. it builds an XNAT request URL."""
    return any(
        isinstance(part, ast.FormattedValue)
        and isinstance(part.value, ast.Name)
        and part.value.id == "XNAT_URL"
        for part in node.values
    )


def _is_encoded(value: ast.expr) -> bool:
    """True if ``value`` is a bare ``XNAT_URL`` reference or a ``seg(...)`` call."""
    if isinstance(value, ast.Name) and value.id in ALLOWED_BARE_NAMES:
        return True
    return isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "seg"


def _offenders_in(path: Path) -> list[str]:
    source = path.read_text()
    lines = source.splitlines()
    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover - source in this tree always parses
        return []

    found: list[str] = []
    for node in (n for n in ast.walk(tree) if isinstance(n, ast.JoinedStr)):
        if not _is_xnat_url_fstring(node):
            continue
        for part in node.values:
            if not isinstance(part, ast.FormattedValue) or _is_encoded(part.value):
                continue
            lineno = getattr(part.value, "lineno", node.lineno)
            rel = path.relative_to(SRC) if path.is_relative_to(SRC) else path.name
            found.append(f"{rel}:{lineno}: {{{ast.unparse(part.value)}}} in {lines[lineno - 1].strip()}")
    return found


def test_source_tree_is_present():
    """Guard the guard: a wrong path would make the assertion below vacuous."""
    assert SRC.is_dir(), f"expected imaging_api source at {SRC}"
    assert _python_sources(), "no python sources found to scan"


def test_every_xnat_url_segment_is_encoded():
    offenders = [o for path in _python_sources() for o in _offenders_in(path)]
    assert not offenders, (
        "Values interpolated into an XNAT URL must be wrapped in `seg(...)` — a raw '?' "
        "starts a query string, '#' truncates, and '/' addresses a different resource:\n  "
        + "\n  ".join(offenders)
    )


def test_scan_actually_looks_at_the_xnat_urls():
    """The scan is worthless if it matches nothing; assert it sees the real call sites."""
    seen = sum(
        1
        for path in _python_sources()
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.JoinedStr) and _is_xnat_url_fstring(node)
    )
    assert seen >= 15, f"expected the scan to reach the XNAT URL call sites, saw {seen}"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # The characters that change what a URL means.
        ("a?format=xml", "a%3Fformat%3Dxml"),
        ("a#fragment", "a%23fragment"),
        ("a/b", "a%2Fb"),  # quote's default safe="/" would leave this through
        ("100%", "100%25"),
        ("a&b=c", "a%26b%3Dc"),
        ("../../admin", "..%2F..%2Fadmin"),
        # Ordinary identifiers are untouched, so existing URLs are unchanged.
        ("PROJECT_123", "PROJECT_123"),
        ("d2f8e0a4-1b3c-4d5e-8f90-abcdef123456", "d2f8e0a4-1b3c-4d5e-8f90-abcdef123456"),
    ],
)
def test_seg_encodes_url_significant_characters(raw, expected):
    assert seg(raw) == expected


def test_seg_coerces_non_strings():
    """Path segments are not all ``str``: pacs_id is an int, prearchive_code an IntEnum."""
    assert seg(7) == "7"


def test_seg_encodes_non_ascii():
    assert seg("Müller") == "M%C3%BCller"
