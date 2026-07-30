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

"""Regression guard: ``except HTTPException: raise`` must not shadow a handler that
was written to catch it.

A generic ``except Exception`` that returns a fixed 500 needs an ``HTTPException``
re-raise in front of it, or a deliberate 403/404 raised upstream gets flattened. But
the same three lines are wrong in front of two other shapes, and applying them
uniformly across the codebase produced real bugs:

* **A handler that reads the exception as an HTTPException** — ``isinstance(e,
  HTTPException)``, ``e.detail``, ``e.status_code``. It was written to catch one.
  Re-raising first makes that branch dead. This is how a definitive 4xx from
  ``set_user_roles`` came to skip the rollback that deletes the just-created Cognito
  user, leaving an orphaned account behind.
* **A handler that does not re-raise at all** — a per-item loop that logs and
  continues, a best-effort notification, a fail-closed access check. Its whole
  purpose is to contain the failure; letting an ``HTTPException`` past it turns a
  contained failure into a request-level one. ``process_trust`` in the approve
  workflow is gathered with ``asyncio.gather`` without ``return_exceptions``, so one
  trust's 404 would have cancelled every sibling and failed the whole approval.

Scope note: this scans flip-api only. The trust services carry the same pattern and
are not covered here — each service has its own suite and no shared test package.
"""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[3] / "src" / "flip_api"

#: Attributes that only exist on an ``HTTPException`` in this codebase — reading one
#: off the caught exception means the handler expects to receive one.
HTTP_EXCEPTION_ATTRS = {"detail", "status_code"}


def _python_sources() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def _is_bare_http_reraise(handler: ast.ExceptHandler) -> bool:
    """True for exactly ``except HTTPException: raise`` (comments don't count as body)."""
    if not (isinstance(handler.type, ast.Name) and handler.type.id == "HTTPException"):
        return False
    return len(handler.body) == 1 and isinstance(handler.body[0], ast.Raise) and handler.body[0].exc is None


def _reads_as_http_exception(handler: ast.ExceptHandler) -> bool:
    """True if the handler inspects its caught exception as an ``HTTPException``."""
    bound = handler.name
    if not bound:
        return False
    for node in ast.walk(handler):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "isinstance"
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == bound
            and "HTTPException" in ast.unparse(node.args[1])
        ):
            return True
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == bound
            and node.attr in HTTP_EXCEPTION_ATTRS
        ):
            return True
    return False


def _offenders_in(path: Path) -> list[str]:
    source = path.read_text()
    lines = source.splitlines()
    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover - source in this tree always parses
        return []

    found: list[str] = []
    for try_node in (n for n in ast.walk(tree) if isinstance(n, ast.Try)):
        for index, handler in enumerate(try_node.handlers):
            if not _is_bare_http_reraise(handler):
                continue
            for later in try_node.handlers[index + 1 :]:
                if _reads_as_http_exception(later):
                    reason = f"handler at line {later.lineno} reads the exception as an HTTPException"
                elif not any(isinstance(n, ast.Raise) for n in ast.walk(later)):
                    reason = f"handler at line {later.lineno} contains the failure instead of re-raising"
                else:
                    continue
                rel = path.relative_to(SRC) if path.is_relative_to(SRC) else path.name
                found.append(f"{rel}:{handler.lineno}: {lines[handler.lineno - 1].strip()} — {reason}")
    return found


def test_source_tree_is_present():
    """Guard the guard: a wrong path would make the assertion below vacuous."""
    assert SRC.is_dir(), f"expected flip_api source at {SRC}"
    assert _python_sources(), "no python sources found to scan"


def test_http_exception_reraise_does_not_shadow_the_handler_below_it():
    offenders = [o for path in _python_sources() for o in _offenders_in(path)]
    assert not offenders, (
        "`except HTTPException: raise` must not sit in front of a handler that was "
        "written to catch one, or one that deliberately contains the failure. Drop the "
        "re-raise and leave a comment saying why:\n  " + "\n  ".join(offenders)
    )


_SHADOWS_A_READER = """
try:
    x()
except HTTPException:
    raise
except Exception as e:
    detail = e.detail if isinstance(e, HTTPException) else str(e)
    raise HTTPException(status_code=500, detail=detail)
"""

_SHADOWS_AN_ATTRIBUTE_READER = """
try:
    x()
except HTTPException:
    raise
except Exception as e:
    raise HTTPException(status_code=e.status_code, detail="failed")
"""

_SHADOWS_A_CONTAINING_HANDLER = """
for item in items:
    try:
        handle(item)
    except HTTPException:
        raise
    except Exception:
        logger.exception("skipping")
        continue
"""


@pytest.mark.parametrize(
    "snippet",
    [_SHADOWS_A_READER, _SHADOWS_AN_ATTRIBUTE_READER, _SHADOWS_A_CONTAINING_HANDLER],
)
def test_detects_shadowed_handlers(snippet, tmp_path):
    f = tmp_path / "sample.py"
    f.write_text(snippet)
    assert _offenders_in(f), f"guard failed to flag:\n{snippet}"


_GENERIC_500 = """
try:
    x()
except HTTPException:
    raise
except Exception:
    raise HTTPException(status_code=500, detail="Internal server error")
"""

_TYPED_DOMAIN_ERROR_FIRST = """
try:
    x()
except NotFoundError as e:
    raise HTTPException(status_code=404, detail=str(e)) from e
except HTTPException:
    raise
except Exception:
    raise HTTPException(status_code=500, detail="Internal server error")
"""

_CONTAINING_HANDLER_WITH_NO_RERAISE_IN_FRONT = """
for item in items:
    try:
        handle(item)
    except Exception:
        logger.exception("skipping")
        continue
"""


@pytest.mark.parametrize(
    "snippet",
    [_GENERIC_500, _TYPED_DOMAIN_ERROR_FIRST, _CONTAINING_HANDLER_WITH_NO_RERAISE_IN_FRONT],
)
def test_does_not_flag_correct_orderings(snippet, tmp_path):
    f = tmp_path / "sample.py"
    f.write_text(snippet)
    assert not _offenders_in(f), f"guard wrongly flagged:\n{snippet}"
