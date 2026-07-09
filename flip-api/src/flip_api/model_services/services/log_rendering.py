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

"""Display-text composition for the model activity feed.

The FL layer reports **facts** — typed events with structured details
(``FLLogEvent`` rows in ``fl_logs``) — and this module is the single place
those facts become English. Keeping the wording hub-side means copy changes
ship with a flip-api redeploy and never require rebuilding the FL images that
are baked and deployed to every trust.
"""

from flip_api.db.models.main_models import FLLogs
from flip_api.domain.schemas.types import FLLogEvent

_BYTE_UNITS = ["B", "KB", "MB", "GB", "TB"]


def _format_bytes(size_bytes: int) -> str:
    """Render a byte count as a short human-readable size (1024-based).

    Mirrors flip-ui's ``formatBytes`` helper so the activity feed and the file
    cards speak the same units.

    Args:
        size_bytes (int): The payload size in bytes.

    Returns:
        str: e.g. ``"2.3 MB"``; whole bytes are shown without decimals.
    """
    size = float(size_bytes)
    for unit in _BYTE_UNITS:
        if size < 1024 or unit == _BYTE_UNITS[-1]:
            return f"{size_bytes} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size_bytes} B"  # pragma: no cover - unreachable, satisfies mypy


def render_log(row: FLLogs) -> str:
    """Compose the display text for one activity-feed row.

    Free-text rows (exception reports, legacy messages, hub status lines) pass
    through verbatim. Typed event rows are rendered from their structured
    fields. An unrecognised ``event_type`` (e.g. a newer FL image talking to an
    older flip-api) degrades to a readable ``Round N · EVENT_NAME`` line rather
    than failing the whole feed.

    Args:
        row (FLLogs): The stored log row.

    Returns:
        str: The text the UI shows for this row.
    """
    if row.log is not None:
        return row.log

    details = row.details or {}

    if row.event_type == FLLogEvent.ROUND_STARTED:
        return f"Round {row.global_round} initiated · global model dispatched"

    if row.event_type == FLLogEvent.CLIENT_RESULT_RECEIVED:
        size_bytes = details.get("size_bytes")
        if size_bytes is not None:
            return f"Round {row.global_round} weights uploaded · {_format_bytes(size_bytes)}"
        # Evaluation replies carry no weights; claim nothing about them.
        return f"Round {row.global_round} results returned"

    if row.event_type == FLLogEvent.ROUND_AGGREGATED:
        returned, expected = details.get("returned"), details.get("expected")
        if returned is not None and expected is not None:
            return f"Round {row.global_round} aggregated · {returned} of {expected} trusts returned"
        return f"Round {row.global_round} aggregated"

    if row.event_type is not None:
        return f"Round {row.global_round} · {row.event_type}"

    return ""
