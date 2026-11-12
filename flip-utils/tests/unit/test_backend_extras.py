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

"""Each FL backend is an extra (FLIP#1047), and each FL image installs only its own.

The checks run in subprocesses whose import system refuses one backend or both, so a module-scope import
of the wrong backend fails here even though this environment has both:

* every backend-neutral ``flip`` module imports with neither backend;
* every ``flip.flower`` module imports with ``flwr`` but no ``nvflare`` (the Flower images);
* every ``flip.nvflare`` module imports with ``nvflare`` but no ``flwr`` (the FLARE images).

Only the backend packages themselves are refused, not the packages that arrive with them (psutil,
docker, ...): a neutral or Flower module importing one of those passes here and fails in the Flower
image, which only an image build catches.

Test ids avoid the word "nvflare" so the light CI job (``-k "not nvflare"``) still runs them.
"""

from __future__ import annotations

import importlib
import pkgutil
import subprocess
import sys
from pathlib import Path

import pytest

import flip

FLIP_UTILS_ROOT = Path(__file__).resolve().parents[2]
BACKEND_PACKAGES = ("flip.nvflare", "flip.flower")

_BLOCK = """
import importlib
import sys

class _Refuse:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in {blocked!r}:
            raise ModuleNotFoundError(f"No module named {{name!r}}")
        return None

sys.meta_path.insert(0, _Refuse())
"""


def _run_refusing(blocked: set[str], code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", _BLOCK.format(blocked=sorted(blocked)) + code],
        cwd=FLIP_UTILS_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _import_all(modules: list[str]) -> str:
    return f"for name in {modules!r}:\n    importlib.import_module(name)\n"


def _modules(package_path: list[str], prefix: str) -> list[str]:
    return sorted(
        info.name
        for info in pkgutil.walk_packages(package_path, prefix=prefix, onerror=lambda _: None)
        if not info.name.endswith(".__main__")
    )


def _neutral_modules() -> list[str]:
    return [name for name in _modules(flip.__path__, "flip.") if not name.startswith(BACKEND_PACKAGES)]


def test_neutral_modules_import_without_backends():
    modules = _neutral_modules()
    assert "flip.utils" in modules
    assert "flip.export.checkpoint" in modules

    result = _run_refusing({"nvflare", "flwr"}, _import_all(modules))

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("package", "other_backend", "sample"),
    [
        ("flip.flower", "nvflare", "flip.flower.strategy"),
        ("flip.nvflare", "flwr", "flip.nvflare.recipes"),
    ],
    ids=["flwr-without-flare", "flare-without-flwr"],
)
def test_backend_modules_import_without_the_other_backend(package: str, other_backend: str, sample: str):
    backend = importlib.import_module(package)
    modules = [package, *_modules(backend.__path__, f"{package}.")]
    assert sample in modules

    result = _run_refusing({other_backend}, _import_all(modules))

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("package", "extra"), [("flip.nvflare", "nvflare"), ("flip.flower", "flower")], ids=["flare", "flwr"]
)
def test_backend_package_names_its_extra(package: str, extra: str):
    result = _run_refusing({"nvflare", "flwr"}, f"import {package}\n")

    assert result.returncode != 0
    assert f"install flip-utils[{extra}]" in result.stderr
