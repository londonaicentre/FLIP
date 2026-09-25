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

"""Shared fixtures: the PEP 723 scripts under docs/scripts/ loaded as modules (they have no package)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

DOCS_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = DOCS_DIR.parent
SCRIPTS_DIR = DOCS_DIR / "scripts"


def load_script(name: str) -> ModuleType:
    """Import ``docs/scripts/<name>.py`` under a test-only module name."""
    spec = importlib.util.spec_from_file_location(f"{name}_under_test", SCRIPTS_DIR / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def fetcher() -> ModuleType:
    return load_script("fetch_docs_gifs")


@pytest.fixture(scope="session")
def publisher() -> ModuleType:
    return load_script("publish_docs_gifs")
