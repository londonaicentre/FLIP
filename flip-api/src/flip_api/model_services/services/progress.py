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

"""Round-state derivation for ``GET /model/{id}/progress``.

The single implementation of federated-round math, shared by every consumer so
no client re-implements it. Derivation is deliberately **max-based, not
sequence-based**: events arrive over best-effort HTTP posts, so a duplicated or
dropped individual event must not shift the derived state.

``derive_progress`` is a pure function over already-fetched rows — unit-testable
without a database and refinable (e.g. the ``DROPPED`` heuristic) without
touching FL images or the UI.
"""

from datetime import datetime
from statistics import mean
from uuid import UUID

from flip_api.db.models.main_models import FLLogs
from flip_api.domain.interfaces.model import (
    IModelProgress,
    IModelProgressTrust,
    ITrustSummary,
    ProgressTrustState,
)
from flip_api.domain.schemas.types import FLLogEvent


def _earliest_per_round(rows: list[FLLogs]) -> list[datetime]:
    """Timestamps of the first occurrence of each distinct round, in round order."""
    first_seen: dict[int, datetime] = {}
    for row in rows:
        if row.global_round is None or row.log_date is None:
            continue
        if row.global_round not in first_seen or row.log_date < first_seen[row.global_round]:
            first_seen[row.global_round] = row.log_date
    return [first_seen[r] for r in sorted(first_seen)]


def _mean_gap_seconds(stamps: list[datetime]) -> float | None:
    """Mean gap between consecutive timestamps; None with fewer than two."""
    if len(stamps) < 2:
        return None
    return mean((b - a).total_seconds() for a, b in zip(stamps, stamps[1:]))


def derive_progress(rows: list[FLLogs], roster: list[ITrustSummary], now: datetime) -> IModelProgress:
    """Fold a model's log rows into the round-progress view.

    Args:
        rows (list[FLLogs]): Every ``fl_logs`` row for the model — typed events
            and trust-attributed free-text rows (the error signal) alike.
        roster (list[ITrustSummary]): The model's participating trusts, from
            ``ModelTrustIntersect`` (populated at dispatch).
        now (datetime): The reference clock for the remaining-time estimate,
            injected for deterministic tests.

    Returns:
        IModelProgress: Round position, timing estimates and one ladder row per
        participating trust. All-null (roster only) when no events exist yet.
    """
    dated = [r for r in rows if r.log_date is not None]

    round_started = [
        r for r in dated if r.event_type == FLLogEvent.ROUND_STARTED and r.global_round is not None
    ]
    current_round = max((r.global_round for r in round_started), default=None)  # type: ignore[type-var]

    # The latest ROUND_STARTED owns the round total: a re-run with a different
    # count is then correct by construction (no write-once staleness).
    latest_start = max(round_started, key=lambda r: r.log_date, default=None)  # type: ignore[arg-type, return-value]
    total_rounds = (latest_start.details or {}).get("total_rounds") if latest_start is not None else None

    start_stamps = _earliest_per_round(round_started)
    started_at = start_stamps[0] if start_stamps else None
    avg_round_seconds = _mean_gap_seconds(start_stamps)

    est_remaining_seconds = None
    if avg_round_seconds is not None and total_rounds is not None and current_round is not None:
        # Rounds still to complete (the running one included), minus how long
        # the running round has already been going.
        running_for = (now - start_stamps[-1]).total_seconds()
        est_remaining_seconds = max(0.0, avg_round_seconds * (total_rounds - current_round + 1) - running_for)

    trusts = []
    for trust in roster:
        trust_rows = [r for r in dated if r.trust == trust.id]
        results = [r for r in trust_rows if r.event_type == FLLogEvent.CLIENT_RESULT_RECEIVED and r.success]
        errors = [r for r in trust_rows if not r.success]

        last_round = max((r.global_round for r in results if r.global_round is not None), default=None)
        last_event_at = max((r.log_date for r in trust_rows), default=None)  # type: ignore[type-var]

        # Dropped = errored with no successful result since. An error followed by
        # a later upload means the trust came back (e.g. IGNORE_RESULT_ERROR runs).
        last_error_at = max((r.log_date for r in errors), default=None)  # type: ignore[type-var]
        last_result_at = max((r.log_date for r in results), default=None)  # type: ignore[type-var]
        dropped = last_error_at is not None and (last_result_at is None or last_result_at < last_error_at)

        if dropped:
            state = ProgressTrustState.DROPPED
        elif last_round is not None and last_round == current_round:
            state = ProgressTrustState.RETURNED
        else:
            state = ProgressTrustState.TRAINING

        trusts.append(
            IModelProgressTrust(  # type: ignore[call-arg]
                id=trust.id,
                name=trust.name,
                code=trust.code,
                last_round=last_round,
                state=state,
                avg_round_seconds=_mean_gap_seconds(_earliest_per_round(results)),
                last_event_at=last_event_at,
            )
        )

    return IModelProgress(  # type: ignore[call-arg]
        current_round=current_round,
        total_rounds=total_rounds,
        started_at=started_at,
        avg_round_seconds=avg_round_seconds,
        est_remaining_seconds=est_remaining_seconds,
        trusts=trusts,
    )


def get_model_progress(model_id: UUID, session, now: datetime | None = None) -> IModelProgress:
    """Fetch a model's rows + roster and derive its round progress.

    Args:
        model_id (UUID): The model to derive progress for.
        session: The database session.
        now (datetime | None): Override clock for tests; defaults to utcnow.

    Returns:
        IModelProgress: See ``derive_progress``.
    """
    from sqlmodel import select

    from flip_api.model_services.services.model_service import _run_trusts_by_model

    rows = list(session.exec(select(FLLogs).where(FLLogs.model_id == model_id)).all())
    roster = _run_trusts_by_model([model_id], session).get(model_id, [])
    return derive_progress(rows, roster, now=now or datetime.utcnow())
