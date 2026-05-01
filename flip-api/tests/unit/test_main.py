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

"""Tests for flip_api.main rate limit handler and CORS configuration."""

import asyncio
from unittest.mock import MagicMock

import pytest
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from flip_api import main
from flip_api.main import app, rate_limit_exceeded_handler


class TestRateLimitExceededHandler:
    def test_returns_429_with_detail(self):
        """rate_limit_exceeded_handler should return a 429 JSON response."""
        request = MagicMock()
        exc = MagicMock()

        response = asyncio.get_event_loop().run_until_complete(rate_limit_exceeded_handler(request, exc))

        assert response.status_code == 429
        assert b"Rate limit exceeded" in response.body


class TestCORSConfiguration:
    """Verify CORS is configured with an explicit allowlist (not wildcard) given allow_credentials=True."""

    @pytest.fixture
    def cors_origins(self):
        """Populate the lifespan-managed allowlist for the duration of a test, then reset it."""
        original = list(main._cors_allowed_origins)
        main._cors_allowed_origins.clear()
        main._cors_allowed_origins.append("https://app.flip.aicentre.co.uk")
        yield main._cors_allowed_origins
        main._cors_allowed_origins.clear()
        main._cors_allowed_origins.extend(original)

    def test_cors_middleware_uses_mutable_allowlist_not_wildcard(self):
        """CORSMiddleware must be registered with the lifespan-populated mutable allowlist, never ['*']."""
        cors_entries = [m for m in app.user_middleware if m.cls is CORSMiddleware]
        assert len(cors_entries) == 1, "Expected exactly one CORSMiddleware registration"

        kwargs = cors_entries[0].kwargs
        assert kwargs["allow_credentials"] is True
        # The middleware holds the same list object that lifespan populates — mutations propagate.
        assert kwargs["allow_origins"] is main._cors_allowed_origins
        assert "*" not in kwargs["allow_origins"], "Wildcard origins are unsafe with allow_credentials=True"

    def test_allowed_origin_is_reflected_with_vary_header(self, cors_origins):
        """A request from a configured origin must get ACAO reflected back with Vary: Origin."""
        allowed_origin = cors_origins[0]
        # Bare TestClient (no context manager) skips the lifespan/scheduler — we drive the
        # allowlist directly via the cors_origins fixture.
        client = TestClient(app)
        response = client.get("/api/health", headers={"Origin": allowed_origin})

        assert response.status_code == 200
        assert response.headers.get("access-control-allow-origin") == allowed_origin
        assert response.headers.get("access-control-allow-credentials") == "true"
        # Vary: Origin tells caches the response varies by Origin — required when reflecting per-origin.
        assert "origin" in response.headers.get("vary", "").lower()

    def test_disallowed_origin_does_not_receive_acao(self, cors_origins):
        """A request from an origin outside the allowlist must not be granted CORS access."""
        client = TestClient(app)
        response = client.get("/api/health", headers={"Origin": "https://evil.example.com"})

        # The endpoint itself still answers (CORS is enforced by the browser, not the server),
        # but no Access-Control-Allow-Origin header may be sent for an unlisted origin.
        assert response.status_code == 200
        assert "access-control-allow-origin" not in {k.lower() for k in response.headers.keys()}
