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

"""HTTP security headers middleware for the flip-api FastAPI application.

Injects defensive headers on every response. These headers are safe for
both HTML (SPA) and JSON (API) responses. Content-Security-Policy is
included for text/html responses only; the SPA primarily relies on
CloudFront edge CSP for HTML assets.
"""

import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

logger = logging.getLogger(__name__)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Inject standard HTTP security headers on every response.

    These headers are safe for both HTML (SPA) and JSON (API) responses.

    Content-Security-Policy is only added for ``text/html`` responses
    because flip-api is JSON-first: the SPA itself is served from
    CloudFront + S3 and its CSP is set at the CloudFront edge. flip-api
    only emits HTML on incidental error pages (e.g. unhandled-exception
    fallbacks), where ``default-src 'self'`` plus the explicit
    ``script-src``/``style-src``/``object-src``/``frame-ancestors``
    directives below are sufficient. ``connect-src``, ``img-src``, and
    ``font-src`` are intentionally omitted: they all fall back to
    ``default-src 'self'``, which already blocks third-party loads.

    The FastAPI docs routes (``/api/docs``, ``/api/redoc``,
    ``/api/openapi.json``) are explicitly excluded from CSP injection:
    Swagger UI and ReDoc ship inline scripts and load their bundles
    from ``cdn.jsdelivr.net``, both of which the default
    ``script-src 'self'`` would block. These pages are off in
    production (``main.py`` sets ``docs_url=None`` when
    ``ENV == "production"``); the exclusion only keeps them usable in
    dev/stag. All other security headers (HSTS, XFO, XCTO, RP) still
    apply to docs routes.
    """

    SECURITY_HEADERS = {
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
        "X-Frame-Options": "DENY",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "strict-origin-when-cross-origin",
    }

    # Paths whose responses are exempt from CSP injection. Swagger UI
    # and ReDoc use inline scripts and load bundles from
    # cdn.jsdelivr.net, which the strict CSP below would block; the
    # OpenAPI schema endpoint is included so the docs UIs can fetch it.
    DOCS_PATHS = ("/api/docs", "/api/redoc", "/api/openapi.json")

    CSP = (
        "default-src 'self';"
        " style-src 'self';"
        " script-src 'self';"
        " object-src 'none';"
        " frame-ancestors 'none';"
    )

    async def dispatch(self, request: Request, call_next) -> Response:
        """Apply security headers to every response.

        Wraps ``call_next`` in try/except so that security headers are
        injected even when the downstream handler raises an unhandled
        exception. On the exception path the traceback is logged and a
        500 fallback response is returned with the same security
        headers — this is more secure than re-raising and letting the
        framework's outermost error middleware produce a bare response
        with no headers.

        Args:
            request: The incoming HTTP request.
            call_next: The next middleware or route handler in the chain.

        Returns:
            Response with security headers injected.
        """
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("Unhandled exception processing request")
            response = PlainTextResponse("Internal Server Error", status_code=500)
        for header, value in self.SECURITY_HEADERS.items():
            response.headers[header] = value
        # CSP only applies to HTML responses — no-op for API JSON. Skip
        # the docs UI paths so Swagger/ReDoc keep working in dev/stag.
        content_type = response.headers.get("content-type", "")
        if "text/html" in content_type and not request.url.path.startswith(self.DOCS_PATHS):
            response.headers["Content-Security-Policy"] = self.CSP
        return response
