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

"""Each FL backend is an extra (FLIP#1047): only ``flip.nvflare`` / ``flip.flower`` may need one.

The checks run in a subprocess whose import system refuses ``nvflare`` and ``flwr``, so a module-scope
backend import added to a backend-neutral module fails here even though this environment has both.
"""

from __future__ import annotations

import pkgutil
import subprocess
import sys
from pathlib import Path

import pytest

import flip

FLIP_UTILS_ROOT = Path(__file__).resolve().parents[2]

_BLOCK_BACKENDS = """
import sys

class _NoBackends:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in {"nvflare", "flwr"}:
            raise ModuleNotFoundError(f"No module named {name!r}")
        return None

sys.meta_path.insert(0, _NoBackends())
"""


def _run_without_backends(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", _BLOCK_BACKENDS + code],
        cwd=FLIP_UTILS_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _neutral_modules() -> list[str]:
    backend_packages = ("flip.nvflare", "flip.flower")
    return sorted(
        info.name
        for info in pkgutil.walk_packages(flip.__path__, prefix="flip.", onerror=lambda _: None)
        if not info.name.startswith(backend_packages) and not info.name.endswith(".__main__")
    )


def test_neutral_modules_import_without_backends():
    modules = _neutral_modules()
    assert "flip.utils" in modules
    assert "flip.export.checkpoint" in modules

    result = _run_without_backends(f"import importlib\nfor name in {modules!r}:\n    importlib.import_module(name)\n")

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("package", "extra"), [("flip.nvflare", "nvflare"), ("flip.flower", "flower")], ids=["flare", "flwr"]
)
def test_backend_package_names_its_extra(package: str, extra: str):
    result = _run_without_backends(f"import {package}\n")

    assert result.returncode != 0
    assert f"install flip-utils[{extra}]" in result.stderr
