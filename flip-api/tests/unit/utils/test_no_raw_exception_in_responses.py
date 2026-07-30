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
ARNs and operation detail, httpx leaks internal hostnames — all of it useful
reconnaissance for an authenticated caller. Handlers must return a fixed message and
let the correlation id tie the response back to the logged traceback.

The check walks the AST rather than matching text: it resolves the name each
``except`` clause binds, so it cannot be defeated by calling the variable ``ve`` or
``exc`` instead of ``e`` — a gap that a regex version of this guard did miss.

Opt-out: a handler that surfaces a message *we* wrote (a domain error such as
"A trust named X already exists") marks the line with ``# safe-detail: <reason>``.
Those exceptions must be a dedicated type, not a bare ``ValueError``, so the message
is provably ours and not something escaping from a library.
"""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[3] / "src" / "flip_api"

#: Keyword arguments whose value is returned to a caller (HTTP body, task result).
CLIENT_FACING_KWARGS = {"detail", "message"}
#: Dict keys returned to a caller (task results posted back to the hub).
CLIENT_FACING_KEYS = {"detail", "message", "error"}
#: Locals conventionally assigned then passed as ``detail=``.
CLIENT_FACING_NAMES = {"error_message"}


def _python_sources() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def _mentions(node: ast.AST, name: str) -> bool:
    """True if ``node`` reads ``name`` into the produced value.

    A conditional's *test* is excluded: ``detail="..." if isinstance(e, X) else "..."``
    inspects the exception to choose a message without putting it in one.
    """
    if isinstance(node, ast.IfExp):
        return _mentions(node.body, name) or _mentions(node.orelse, name)
    return any(isinstance(n, ast.Name) and n.id == name for n in ast.walk(node))


def _offenders_in(path: Path) -> list[str]:
    source = path.read_text()
    lines = source.splitlines()
    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover - source in this tree always parses
        return []

    # Dicts handed to a logger are log records, not responses.
    logged: set[int] = set()
    for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
        func = call.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "logger":
            logged.update(id(a) for a in ast.walk(call) if isinstance(a, ast.Dict))

    found: list[str] = []
    for handler in (n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)):
        bound = handler.name
        if not bound:
            continue  # `except X:` binds nothing, so nothing can leak
        for node in ast.walk(handler):
            hits: list[ast.AST] = []
            if isinstance(node, ast.Call):
                hits += [kw.value for kw in node.keywords if kw.arg in CLIENT_FACING_KWARGS]
            elif isinstance(node, ast.Dict) and id(node) not in logged:
                # `return {"error": str(e)}` — how a task result reaches the hub.
                hits += [
                    v
                    for k, v in zip(node.keys, node.values)
                    if isinstance(k, ast.Constant) and k.value in CLIENT_FACING_KEYS
                ]
            elif isinstance(node, ast.Assign):
                if any(isinstance(t, ast.Name) and t.id in CLIENT_FACING_NAMES for t in node.targets):
                    hits.append(node.value)
            for value in hits:
                if not _mentions(value, bound):
                    continue
                lineno = getattr(value, "lineno", handler.lineno)
                # The marker sits above the statement, which may span several lines.
                window = lines[max(0, lineno - 7) : lineno]
                if any("safe-detail:" in w for w in window):
                    continue
                rel = path.relative_to(SRC) if path.is_relative_to(SRC) else path.name
                found.append(f"{rel}:{lineno}: {lines[lineno - 1].strip()}")
    return found


def test_source_tree_is_present():
    """Guard the guard: a wrong path would make every assertion below vacuous."""
    assert SRC.is_dir(), f"expected flip_api source at {SRC}"
    assert _python_sources(), "no python sources found to scan"


def test_no_raw_exception_text_in_client_facing_messages():
    offenders = [o for path in _python_sources() for o in _offenders_in(path)]
    assert not offenders, (
        "Raw exception text must not be returned to callers — use a fixed message and "
        "let the correlation id link it to the logged traceback. If the message is one "
        "we wrote (a dedicated domain exception), mark the line '# safe-detail: <reason>':\n  "
        + "\n  ".join(offenders)
    )


@pytest.mark.parametrize(
    "snippet",
    [
        # the shapes the original regex caught
        'try:\n    x()\nexcept Exception as e:\n    raise HTTPException(status_code=500, detail=str(e))',
        'try:\n    x()\nexcept Exception as e:\n    raise HTTPException(detail=f"failed: {e}")',
        'try:\n    x()\nexcept Exception as e:\n    error_message = f"failed: {str(e)}"',
        # the shape it missed: a differently-named binding
        'try:\n    x()\nexcept ValueError as ve:\n    raise HTTPException(status_code=400, detail=str(ve))',
        'try:\n    x()\nexcept Exception as exc:\n    return {"ok": False, "message": str(exc)}',
    ],
)
def test_detects_leaks_regardless_of_variable_name(snippet, tmp_path):
    f = tmp_path / "sample.py"
    f.write_text(snippet)
    assert _offenders_in(f), f"guard failed to flag:\n{snippet}"


@pytest.mark.parametrize(
    "snippet",
    [
        'try:\n    x()\nexcept Exception:\n    raise HTTPException(status_code=500, detail="Internal server error")',
        # logging the exception server-side is the point, not a leak
        'try:\n    x()\nexcept Exception as e:\n    logger.exception(f"failed: {e}")',
        # explicit, justified opt-out
        'try:\n    x()\nexcept DuplicateTrustError as e:\n'
        '    # safe-detail: domain error, message is author-written\n'
        '    raise HTTPException(status_code=409, detail=str(e))',
    ],
)
def test_does_not_flag_safe_shapes(snippet, tmp_path):
    f = tmp_path / "sample.py"
    f.write_text(snippet)
    assert not _offenders_in(f), f"guard wrongly flagged:\n{snippet}"
