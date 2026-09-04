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
# Drift guard: every `flwr` import in a Flower app must resolve against the installed flwr.
#
# flwr moves things between releases while keeping the names re-exported on the package — 1.32
# deleted `flwr.common.record` and `flwr.common.message`, but `flwr.common.ConfigRecord` and
# `flwr.common.Message` still exist. An app pinned to the old dotted path therefore keeps passing
# review, ruff and every existing test, and dies only at runtime.
#
# It dies badly: the import is at module scope, so the app fails before any of its code runs and
# Flower reports exit code 607 — which reads like a stale image rather than a bad import. That is
# what happened to the evaluation apps between the flwr>=1.32 bump (2026-07-29) and this guard:
# nothing in CI ever imported them. `make lint` runs ruff, which does not resolve imports, and the
# only test that touches these files parses them with `ast`.
#
# Static rather than an import of the apps themselves: importing a client_app pulls in torch and
# MONAI and runs app module-scope code, which is neither cheap nor side-effect free in CI. Reading
# the import statements and resolving them against the real flwr catches this class at the same
# depth for none of the cost.

from __future__ import annotations

import ast
import importlib
import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
# Both trees ship Flower apps: the tutorials researchers copy, and the templates they scaffold from.
APP_ROOTS = (REPO_ROOT / "fl-tutorials" / "flower", REPO_ROOT / "fl-apps" / "flower")


def _flower_app_sources() -> list[Path]:
    return sorted(p for root in APP_ROOTS for p in root.glob("*/app/*.py") if p.name != "__init__.py")


def _flwr_imports(source: Path) -> list[tuple[str, tuple[str, ...]]]:
    """Every ``from flwr... import a, b`` in one file, as (module, names)."""
    tree = ast.parse(source.read_text(encoding="utf-8"))
    return [
        (node.module, tuple(alias.name for alias in node.names))
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level == 0 and (node.module or "").split(".")[0] == "flwr"
    ]


@pytest.mark.parametrize("source", _flower_app_sources(), ids=lambda p: f"{p.parent.parent.name}/{p.name}")
def test_flwr_imports_resolve(source: Path) -> None:
    """Each imported flwr module exists, and actually exports the names taken from it."""
    for module, names in _flwr_imports(source):
        assert importlib.util.find_spec(module) is not None, (
            f"{source.relative_to(REPO_ROOT)} imports from '{module}', which the installed flwr does not "
            f"have. flwr moves modules between releases while keeping the names re-exported — try "
            f"'flwr.app' or 'flwr.common'."
        )
        imported = importlib.import_module(module)
        missing = [name for name in names if not hasattr(imported, name)]
        assert not missing, f"{source.relative_to(REPO_ROOT)}: '{module}' no longer exports {missing}."
