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
    Content-Security-Policy is only added for responses with a ``text/html``
    content type; for API JSON responses (the norm) CSP is not set by this
    middleware — only at the CloudFront edge for the SPA.
    """

    SECURITY_HEADERS = {
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
        "X-Frame-Options": "DENY",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "strict-origin-when-cross-origin",
    }

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
        exception.  On the exception path the traceback is logged and a
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
        # CSP only applies to HTML responses — no-op for API JSON.
        content_type = response.headers.get("content-type", "")
        if "text/html" in content_type:
            response.headers["Content-Security-Policy"] = self.CSP
        return response
