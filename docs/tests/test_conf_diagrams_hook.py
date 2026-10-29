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

"""The ``builder-inited`` hook in ``docs/source/conf.py`` that renders the architecture diagrams.

Every diagram script renders into the same directory, and each tree checks only its own picture names, so a
name two trees share would replace one figure with another. These tests drive the hook with stub scripts.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import DOCS_DIR
from sphinx.errors import SphinxError


@pytest.fixture
def conf(monkeypatch):
    spec = importlib.util.spec_from_file_location("conf_diagrams_under_test", DOCS_DIR / "source" / "conf.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.delenv(module.SKIP_DIAGRAMS_ENV, raising=False)
    monkeypatch.setattr(module, "DIAGRAM_SCRIPTS", (Path("hub.py"), Path("node.py")))
    return module


def stub_scripts(conf, monkeypatch, names: dict[str, list[str]]) -> None:
    out = conf.GENERATED_ASSETS_DIR
    monkeypatch.setattr(
        conf,
        "_load_diagram_script",
        lambda path: SimpleNamespace(render=lambda _out: [out / name for name in names[path.name]]),
    )


def test_two_scripts_writing_one_picture_fail_the_build(conf, monkeypatch):
    stub_scripts(conf, monkeypatch, {"hub.py": ["aws-a.png"], "node.py": ["aws-a.png"]})
    with pytest.raises(SphinxError, match=r"node\.py renders aws-a\.png"):
        conf._render_generated_figures(app=None)


def test_distinct_pictures_render(conf, monkeypatch):
    stub_scripts(conf, monkeypatch, {"hub.py": ["hub-a.png"], "node.py": ["node-a.png"]})
    conf._render_generated_figures(app=None)
