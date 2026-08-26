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

"""Tests for imaging_api.main app construction (docs gating, lifespan background services)."""

import asyncio
import importlib
import threading
from types import SimpleNamespace

from fastapi.testclient import TestClient

from imaging_api import config, main
from imaging_api.utils.background import dead_background_tasks, reset_dead_background_tasks


class TestDocsGating:
    """Swagger UI / OpenAPI / ReDoc must be disabled in production environments."""

    def test_docs_urls_set_in_dev(self):
        """Tests run with ENV=development, so the live app must expose all three URLs."""
        assert main.app.docs_url == "/docs"
        assert main.app.openapi_url == "/openapi.json"
        assert main.app.redoc_url == "/redoc"

    def test_docs_urls_none_in_production(self, monkeypatch):
        """With ENV=production, the FastAPI app must build with all three URLs unset."""
        monkeypatch.setattr(
            config,
            "_settings",
            SimpleNamespace(ENV="production", TRUST_INTERNAL_SERVICE_KEY="x"),
        )
        try:
            # FastAPI bakes docs_url/openapi_url/redoc_url into the router at app
            # construction time, so patching the live app object after the fact has
            # no effect on routing — we must reload the module to construct a new
            # app under the production settings.
            importlib.reload(main)
            assert main.app.docs_url is None
            assert main.app.openapi_url is None
            assert main.app.redoc_url is None

            client = TestClient(main.app)
            assert client.get("/docs").status_code == 404
            assert client.get("/openapi.json").status_code == 404
            assert client.get("/redoc").status_code == 404
        finally:
            monkeypatch.undo()
            importlib.reload(main)


class TestLifespan:
    """The lifespan starts the cache-retention sweeper (FLIP#1050), gated on its toggle."""

    def test_sweeper_started_and_health_reports_ok(self, monkeypatch):
        started = threading.Event()

        async def fake_sweeper():
            started.set()
            await asyncio.Event().wait()  # run until the lifespan cancels us

        monkeypatch.setattr(main, "run_cache_retention_sweeper", fake_sweeper)
        with TestClient(main.app) as client:
            assert started.wait(timeout=5), "lifespan did not start the retention sweeper"
            response = client.get("/health/")
            assert response.json()["status"] == "ok"
            assert response.json()["dead_tasks"] == []
        # Shutdown cancelled the (still-running) sweeper: cancellation is not a death.
        assert dead_background_tasks() == set()

    def test_sweeper_not_started_when_disabled(self, monkeypatch):
        started = threading.Event()

        async def fake_sweeper():
            started.set()

        monkeypatch.setattr(main, "run_cache_retention_sweeper", fake_sweeper)
        monkeypatch.setattr(config.get_settings(), "IMAGE_CACHE_RETENTION_ENABLED", False)
        with TestClient(main.app) as client:
            assert client.get("/health/").json()["status"] == "ok"
        assert not started.is_set()

    def test_dead_sweeper_surfaces_as_degraded(self, monkeypatch):
        async def dying_sweeper():
            raise RuntimeError("sweeper blew up")

        monkeypatch.setattr(main, "run_cache_retention_sweeper", dying_sweeper)
        try:
            with TestClient(main.app) as client:
                # The death is recorded via a done-callback on the app's event loop;
                # each request round-trip lets that loop run, so poll a few times
                # rather than sleeping (bounded — never an unbounded retry loop).
                for _ in range(50):
                    body = client.get("/health/").json()
                    if body["status"] == "degraded":
                        break
                assert body["status"] == "degraded"
                assert body["dead_tasks"] == ["image_cache_retention"]
        finally:
            reset_dead_background_tasks()
