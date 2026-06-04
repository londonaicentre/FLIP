# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Test that SecurityHeadersMiddleware adds the expected headers."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from flip_api.utils.security_headers import SecurityHeadersMiddleware


def test_security_headers_middleware_adds_all_headers():
    """Verify all four security headers are present on every response."""
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/test")
    async def test_route():
        return {"ok": True}

    client = TestClient(app)
    response = client.get("/test")

    assert response.status_code == 200
    assert response.headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def test_security_headers_on_unhandled_exception():
    """Verify security headers are present even on unhandled exceptions.

    The middleware catches exceptions from ``call_next`` and returns a
    500 fallback response with the same security headers attached.
    """
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/error")
    async def error_route():
        raise ValueError("simulated unhandled error")

    client = TestClient(app)
    response = client.get("/error")

    assert response.status_code == 500
    assert response.text == "Internal Server Error"
    assert response.headers["strict-transport-security"] == \
        "max-age=31536000; includeSubDomains"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == \
        "strict-origin-when-cross-origin"


def test_security_headers_no_csp_on_json_response():
    """Verify CSP is NOT added for JSON responses (the norm for APIs)."""
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/json")
    async def json_route():
        return {"hello": "world"}

    client = TestClient(app)
    response = client.get("/json")

    assert response.status_code == 200
    assert "content-type" in response.headers
    assert "application/json" in response.headers["content-type"]
    # CSP should NOT be present for JSON responses
    assert "content-security-policy" not in response.headers


def test_security_headers_csp_on_html_response():
    """Verify CSP IS added for text/html responses."""
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/html")
    async def html_route():
        from fastapi.responses import HTMLResponse
        return HTMLResponse("<html><body><h1>Test</h1></body></html>")

    client = TestClient(app)
    response = client.get("/html")

    assert response.status_code == 200
    assert "content-type" in response.headers
    assert "text/html" in response.headers["content-type"]
    # CSP should be present for HTML responses
    assert "content-security-policy" in response.headers
    csp = response.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "style-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "object-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp


def test_security_headers_no_csp_on_docs_routes():
    """Verify CSP is NOT added on docs routes so Swagger/ReDoc can load CDN assets."""
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    for path in ("/api/docs", "/api/redoc", "/api/openapi.json", "/api/docs/oauth2-redirect"):
        @app.get(path)
        async def docs_route():
            from fastapi.responses import HTMLResponse
            return HTMLResponse("<html></html>")

        client = TestClient(app)
        response = client.get(path)

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        # Other security headers still present
        assert "strict-transport-security" in response.headers
        assert "x-frame-options" in response.headers
        assert "x-content-type-options" in response.headers
        assert "referrer-policy" in response.headers
        # CSP should NOT be present on docs routes
        assert "content-security-policy" not in response.headers, \
            f"CSP unexpectedly present on {path}"
