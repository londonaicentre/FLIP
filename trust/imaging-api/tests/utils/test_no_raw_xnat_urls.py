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

"""Guard: every XNAT URL in imaging_api is built by ``xnat_url`` (#1386).

The walk is over the AST rather than the source text, so a docstring or comment that quotes
``f"{XNAT_URL}/..."`` is a string constant or nothing at all and never trips it. What it forbids
is string *building* that touches ``XNAT_URL`` — an f-string, ``+``/``%`` on strings, or
``.format(...)`` — anywhere outside the helper module, which is the one place allowed to join the
base URL to its quoted segments.
"""

import ast
from pathlib import Path
from typing import TypeGuard

import imaging_api

PACKAGE_DIR = Path(imaging_api.__file__).resolve().parent
HELPER_MODULE = PACKAGE_DIR / "utils" / "xnat_url.py"


def _is_xnat_url_ref(node: ast.AST) -> bool:
    """``XNAT_URL`` as a bare name, or as an attribute (``settings.XNAT_URL``, ``get_settings().XNAT_URL``)."""
    return (isinstance(node, ast.Name) and node.id == "XNAT_URL") or (
        isinstance(node, ast.Attribute) and node.attr == "XNAT_URL"
    )


def _references_xnat_url(node: ast.AST) -> bool:
    return any(_is_xnat_url_ref(child) for child in ast.walk(node))


def _is_namespace_registration(node: ast.AST) -> bool:
    """``ET.register_namespace("xnat", ...)`` — the XML namespace URI, not a request (services/projects.py)."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "register_namespace"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "ET"
        and len(node.args) == 2
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "xnat"
    )


def _builds_a_string(node: ast.AST) -> TypeGuard[ast.expr]:
    """An f-string, ``+`` or ``%`` on operands, or a ``.format(...)`` call."""
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        return True
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format"


def find_raw_xnat_urls(source: str) -> list[int]:
    """Return the line numbers where a string is built from ``XNAT_URL`` outside ``xnat_url``."""
    tree = ast.parse(source)
    exempt = {id(child) for node in ast.walk(tree) if _is_namespace_registration(node) for child in ast.walk(node)}
    return sorted(
        {
            node.lineno
            for node in ast.walk(tree)
            if id(node) not in exempt and _builds_a_string(node) and _references_xnat_url(node)
        }
    )


def _package_modules() -> list[Path]:
    return sorted(PACKAGE_DIR.rglob("*.py"))


def test_walk_covers_the_package():
    """A wrong path must not make the guard pass vacuously."""
    modules = _package_modules()
    assert HELPER_MODULE in modules
    relative = {path.relative_to(PACKAGE_DIR).as_posix() for path in modules}
    assert {"services/projects.py", "services/users.py", "services/imaging.py", "routers/users.py"} <= relative
    assert len(modules) > 20


def test_no_xnat_url_is_built_outside_the_helper():
    offenders = [
        f"{path.relative_to(PACKAGE_DIR.parent).as_posix()}:{line}"
        for path in _package_modules()
        if path != HELPER_MODULE
        for line in find_raw_xnat_urls(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], (
        "Build XNAT URLs with imaging_api.utils.xnat_url.xnat_url(*segments, query=...), "
        f"not by interpolating XNAT_URL: {offenders}"
    )


def test_detector_flags_each_way_of_building_a_url():
    source = "\n".join(
        [
            'a = f"{XNAT_URL}/data/projects"',
            'b = XNAT_URL + "/xapi/users"',
            'c = "{}/xapi/pacs".format(get_settings().XNAT_URL)',
            'd = "%s/data/experiments" % settings.XNAT_URL',
            'requests.delete(f"{XNAT_URL}/data/projects/{project_id}?removeFiles=true")',
            'ET.register_namespace("other", f"{XNAT_URL}/data/projects")',
        ]
    )
    assert find_raw_xnat_urls(source) == [1, 2, 3, 4, 5, 6]


def test_detector_ignores_docstrings_comments_and_the_namespace_registration():
    source = '''
def get_subjects(project_id):
    """Fetch subjects.

    ``GET f"{XNAT_URL}/data/projects/{project_id}/subjects"``
    """
    # requests.get(f"{XNAT_URL}/data/projects")
    return requests.get(xnat_url("data", "projects", project_id, "subjects"))


ET.register_namespace("xnat", f"{XNAT_URL}/data/projects")
XNAT_URL = get_settings().XNAT_URL
'''
    assert find_raw_xnat_urls(source) == []
