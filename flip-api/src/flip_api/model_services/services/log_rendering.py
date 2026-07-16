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

The reporting layer — the FL images for round events, the hub's FL scheduler
for queue positions — records **facts**: typed events with structured details
(``FLLogEvent`` rows in ``fl_logs``). This module is the single place those
facts become English. Keeping the wording hub-side means copy changes ship
with a flip-api redeploy and never require rebuilding the FL images that are
baked and deployed to every trust.
"""

import math
from typing import Any, TypeGuard

from flip_api.db.models.main_models import FLLogs
from flip_api.domain.schemas.types import FLLogEvent

_BYTE_UNITS = ["B", "KB", "MB", "GB", "TB"]


def _format_bytes(size_bytes: Any) -> str | None:
    """Render a byte count as a short human-readable size (1024-based).

    Matches the 1024-based units of flip-ui's ``formatBytes`` helper so the
    activity feed and the file cards speak the same units. ``details`` is
    untyped JSONB, so any value a sender may have stored must degrade to
    ``None`` (caller renders sizeless) rather than raise — a raising row would
    poison every subsequent serve of the model's feed.

    Args:
        size_bytes (Any): The payload size in bytes, as stored in ``details``.

    Returns:
        str | None: e.g. ``"2.3 MB"`` (whole bytes without decimals), or
        ``None`` when the stored value is not a finite number.
    """
    try:
        size = float(size_bytes)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(size):
        return None
    raw = size
    for unit in _BYTE_UNITS:
        if size < 1024 or unit == _BYTE_UNITS[-1]:
            return f"{int(raw)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{int(raw)} B"  # pragma: no cover - unreachable, satisfies mypy


def _is_count(value: Any) -> TypeGuard[int]:
    """True when a ``details`` value is a genuine integer count (bools excluded)."""
    return isinstance(value, int) and not isinstance(value, bool)


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
        size_text = _format_bytes(details.get("size_bytes"))
        if size_text is not None:
            return f"Round {row.global_round} weights uploaded · {size_text}"
        # Evaluation replies carry no weights (and an unusable stored size must
        # not claim one); say nothing about the payload.
        return f"Round {row.global_round} results returned"

    if row.event_type == FLLogEvent.ROUND_AGGREGATED:
        returned, expected = details.get("returned"), details.get("expected")
        if _is_count(returned) and _is_count(expected):
            return f"Round {row.global_round} aggregated · {returned} of {expected} trusts returned"
        return f"Round {row.global_round} aggregated"

    if row.event_type == FLLogEvent.QUEUE_POSITION:
        position = details.get("position")
        if _is_count(position) and position >= 1:
            return f"Model Queued ({position})"
        # A row with an unusable stored position must not invent one.
        return "Model Queued"

    if row.event_type is not None:
        return f"Round {row.global_round} · {row.event_type}"

    return ""


def render_fallback(row: FLLogs) -> str:
    """Last-resort display text when composing a row's text raised.

    Never raises: plain f-string interpolation over whatever the row holds.
    Callers use this to degrade a single stored row instead of letting it 500
    every subsequent serve of the model's feed.

    Args:
        row (FLLogs): The stored log row that failed to render.

    Returns:
        str: The row's free text, the unknown-event wording, or ``""``.
    """
    if row.log is not None:
        return row.log
    if row.event_type is not None:
        return f"Round {row.global_round} · {row.event_type}"
    return ""
