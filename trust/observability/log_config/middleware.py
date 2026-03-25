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

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from log_config.formatter import request_context

# Request lifecycle event names
REQUEST_STARTED = "request.started"
REQUEST_COMPLETED = "request.completed"
REQUEST_FAILED = "request.failed"

logger = logging.getLogger(__name__)


class LoggingMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware that sets per-request logging context and logs request lifecycle."""

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())

        ctx = {
            "request_id": request_id,
        }
        token = request_context.set(ctx)

        start = time.monotonic()
        logger.info(
            "Request started",
            extra={
                "event": REQUEST_STARTED,
                "method": request.method,
                "path": request.url.path,
            },
        )

        try:
            response = await call_next(request)
        except Exception:
            duration_ms = round((time.monotonic() - start) * 1000)
            logger.exception(
                "Request failed with unhandled exception",
                extra={
                    "event": REQUEST_FAILED,
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": duration_ms,
                },
            )
            raise
        finally:
            request_context.reset(token)

        duration_ms = round((time.monotonic() - start) * 1000)
        logger.info(
            "Request completed",
            extra={
                "event": REQUEST_COMPLETED,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        response.headers["X-Request-ID"] = request_id
        return response
