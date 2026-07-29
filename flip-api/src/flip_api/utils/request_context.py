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

"""Per-request correlation id.

Error responses deliberately carry no exception text — a raw ``str(e)`` from
SQLAlchemy, boto, or httpx hands the caller schema names, internal hostnames, and
query fragments. That leaves support with nothing to search on, so every request
gets an id: returned to the caller, logged alongside the traceback, and the only
thing a user needs to quote for an engineer to find the real error.

The id is **generated here, never read from the request**. Accepting a
client-supplied ``X-Request-ID`` would let a caller forge or collide log entries.
"""

import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"

# Default covers work that runs outside a request (scheduler jobs, CLI entrypoints),
# so callers never have to handle a missing id.
_request_id: ContextVar[str] = ContextVar("request_id", default="-")


def get_request_id() -> str:
    """Return the current request's correlation id, or ``"-"`` outside a request."""
    return _request_id.get()


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Assign each request a correlation id and echo it back on the response."""

    async def dispatch(self, request: Request, call_next) -> Response:
        """Bind a fresh id for the duration of the request.

        Args:
            request: The incoming HTTP request.
            call_next: The next middleware or route handler.

        Returns:
            Response: The downstream response, carrying the id header.
        """
        token = _request_id.set(uuid.uuid4().hex)
        try:
            response = await call_next(request)
            response.headers[REQUEST_ID_HEADER] = get_request_id()
            return response
        finally:
            _request_id.reset(token)
