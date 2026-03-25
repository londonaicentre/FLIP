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

import json
import logging
from contextvars import ContextVar
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Per-request context
# ---------------------------------------------------------------------------

# Holds a dict of fields (request_id, user_id, etc.) that get merged into
# every log record emitted during that request.
request_context: ContextVar[dict | None] = ContextVar("request_context", default=None)

# ---------------------------------------------------------------------------
# JSON formatter
# ---------------------------------------------------------------------------

# Fields from LogRecord that we handle explicitly or want to exclude from extras
_BUILTIN_ATTRS = frozenset({
    "args",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "message",
    "module",
    "msecs",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
    "taskName",
})


class JSONFormatter(logging.Formatter):
    """Outputs log records as single-line JSON objects."""

    def __init__(self, api_name: str) -> None:
        super().__init__()
        self.api_name = api_name

    def format(self, record: logging.LogRecord) -> str:
        entry: dict = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "api": self.api_name,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Merge request-scoped context
        ctx = request_context.get()
        if ctx:
            entry.update(ctx)

        # Merge extra fields passed via logger.info("msg", extra={...})
        for key, val in record.__dict__.items():
            if key not in _BUILTIN_ATTRS and key not in entry:
                entry[key] = val

        if record.exc_info and record.exc_info[1] is not None:
            entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(entry, default=str)
