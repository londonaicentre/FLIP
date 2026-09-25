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
"""The ``builder-inited`` hook in ``docs/source/conf.py`` that fetches the pinned GIFs (FLIP#1236).

``sphinx-build`` runs without ``-W``, so a hook that is disconnected, or that lets a ``FetchError`` through as
something other than ``SphinxError``, would still produce a green build — with 32 "image file not readable"
warnings. These tests drive the hook directly, with the fetcher's network call stubbed.
"""

from __future__ import annotations

import importlib.util
import sys
from types import SimpleNamespace

import pytest
from conftest import DOCS_DIR
from sphinx.errors import SphinxError


class FakeLogger:
    def __init__(self):
        self.warnings: list[str] = []
        self.infos: list[str] = []

    def warning(self, message: str, *args) -> None:
        self.warnings.append(message % args)

    def info(self, message: str, *args) -> None:
        self.infos.append(message % args)


@pytest.fixture(scope="module")
def conf():
    spec = importlib.util.spec_from_file_location("conf_under_test", DOCS_DIR / "source" / "conf.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def hook(conf, fetcher, monkeypatch):
    """The hook, with the real fetcher module bound to the name it imports and its network call stubbed out."""
    monkeypatch.setitem(sys.modules, "fetch_docs_gifs", fetcher)
    monkeypatch.delenv(conf.SKIP_GIF_FETCH_ENV, raising=False)
    monkeypatch.setenv(fetcher.REVISION_ENV, "20260907T122006Z-5945242")
    logger = FakeLogger()
    monkeypatch.setattr(conf, "logger", logger)
    return SimpleNamespace(run=lambda: conf._fetch_docs_gifs(app=None), logger=logger)


def test_the_hook_is_connected_to_builder_inited(conf):
    connected: list[tuple[str, object]] = []
    conf.setup(SimpleNamespace(connect=lambda event, fn: connected.append((event, fn))))
    assert ("builder-inited", conf._fetch_docs_gifs) in connected


def test_a_fetch_error_fails_the_build_naming_the_remedies(hook, fetcher, monkeypatch):
    def fail(*_args, **_kwargs):
        raise fetcher.FetchError("https://example/manifest.json: HTTP 404")

    monkeypatch.setattr(fetcher, "fetch", fail)
    with pytest.raises(SphinxError, match=r"HTTP 404.*\.gifs_version.*FLIP_DOCS_SKIP_GIF_FETCH=1"):
        hook.run()


def test_the_opt_out_warns_and_fetches_nothing(hook, fetcher, monkeypatch, conf):
    monkeypatch.setenv(conf.SKIP_GIF_FETCH_ENV, "1")
    monkeypatch.setattr(fetcher, "fetch", lambda *a, **k: pytest.fail("fetch() must not run under the opt-out"))
    hook.run()
    assert any(conf.SKIP_GIF_FETCH_ENV in message for message in hook.logger.warnings)


def test_a_successful_fetch_logs_its_summary(hook, fetcher, monkeypatch):
    report = fetcher.FetchReport(repo="r", revision="v", version="v", fetched=["flip/x.gif"], verified=[])
    monkeypatch.setattr(fetcher, "fetch", lambda *a, **k: report)
    hook.run()
    assert hook.logger.warnings == []
    assert any(report.summary() in message for message in hook.logger.infos)
