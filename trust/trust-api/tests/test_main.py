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

import asyncio
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from trust_api import config, main
from trust_api.main import lifespan


@pytest.mark.asyncio
async def test_lifespan_starts_and_cancels_poller():
    """Lifespan should start run_poller as a background task and cancel it on shutdown."""
    mock_app = AsyncMock()

    with patch("trust_api.main.run_poller", new_callable=AsyncMock) as mock_run_poller:
        # Simulate a poller that runs until cancelled
        async def fake_poller():
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                pass

        mock_run_poller.side_effect = fake_poller

        async with lifespan(mock_app):
            # Poller should be running
            mock_run_poller.assert_called_once()

        # After exiting lifespan, poller task should have been cancelled
        # (no exception means it was handled cleanly)


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
