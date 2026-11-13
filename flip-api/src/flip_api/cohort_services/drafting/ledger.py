# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
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

"""The monthly drafting allowance (FLIP#1556): reserve before the model call, then consume or release.

Accounting lives in Postgres, so it holds across replicas. A reservation is taken under a row lock on
the user's ``cohort_draft_allowance`` row for the month, so concurrent requests cannot overshoot the
allowance. Only a validated draft is consumed; a clarification, decline or error releases it, and a
reservation older than ``COHORT_DRAFT_RESERVATION_TTL_MINUTES`` (a crashed request) no longer counts.

Every request also counts towards ``COHORT_DRAFT_MONTHLY_REQUEST_CAP``, a hidden monthly cap on model
calls whatever their outcome, retries included: a reservation holds the most calls one request can
make, and is settled to the calls it made.
Times are naive UTC, like the column type.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import StrEnum
from uuid import UUID

from sqlalchemy import case, func, or_, update
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import Session, and_, col, select

from flip_api.cohort_services.drafting.service import MAX_MODEL_CALLS
from flip_api.config import get_settings
from flip_api.db.models.main_models import CohortDraftAllowance, CohortDraftRequest
from flip_api.domain.schemas.status import CohortDraftStatus


class Refusal(StrEnum):
    """Why a reservation was not taken."""

    EXHAUSTED = "allowance_exhausted"
    REQUEST_CAP = "request_cap_reached"
    RATE_LIMITED = "rate_limited"
    DUPLICATE = "duplicate_request"


@dataclass
class Usage:
    """A user's allowance this month."""

    used: int
    limit: int
    resets_at: datetime


def utcnow() -> datetime:
    """Now, as naive UTC."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def period_start(now: datetime) -> date:
    """The first day of ``now``'s UTC month."""
    return date(now.year, now.month, 1)


def next_reset(now: datetime) -> datetime:
    """00:00 UTC on the first day of the next month (timezone-aware, for the API)."""
    year, month = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
    return datetime(year, month, 1, tzinfo=timezone.utc)


def _counts_against_allowance(user_id: UUID, now: datetime):  # noqa: ANN202 - a SQLAlchemy clause
    """Consumed drafts this month, plus reservations still in flight."""
    fresh = now - timedelta(minutes=get_settings().COHORT_DRAFT_RESERVATION_TTL_MINUTES)
    return and_(
        col(CohortDraftRequest.user_id) == user_id,
        col(CohortDraftRequest.period_start) == period_start(now),
        or_(
            col(CohortDraftRequest.status) == CohortDraftStatus.CONSUMED,
            and_(
                col(CohortDraftRequest.status) == CohortDraftStatus.RESERVED,
                col(CohortDraftRequest.created_at) >= fresh,
            ),
        ),
    )


def get_usage(db: Session, user_id: UUID, now: datetime | None = None) -> Usage:
    """The user's allowance this month. Reads only."""
    now = now or utcnow()
    used = db.exec(select(func.count()).select_from(CohortDraftRequest).where(_counts_against_allowance(user_id, now)))
    return Usage(used=used.one(), limit=get_settings().COHORT_DRAFT_MONTHLY_ALLOWANCE, resets_at=next_reset(now))


def reserve(
    db: Session, user_id: UUID, project_id: UUID, idempotency_key: str, now: datetime | None = None
) -> UUID | Refusal:
    """Take one draft from the user's allowance, or say why not. Commits either way.

    Args:
        db (Session): Database session. Committed before returning, so no transaction spans the model call.
        user_id (UUID): The caller.
        project_id (UUID): The project drafted for.
        idempotency_key (str): The client's key for this request, unique per user.
        now (datetime | None): Naive UTC now, for tests.

    Returns:
        UUID | Refusal: The reservation id, or the reason none was taken.
    """
    now = now or utcnow()
    settings = get_settings()
    period = period_start(now)
    try:
        db.execute(
            insert(CohortDraftAllowance)
            .values(user_id=user_id, period_start=period)
            .on_conflict_do_nothing(index_elements=["user_id", "period_start"])
        )
        # Serialises this user's reservations until the commit below, on every replica.
        db.exec(
            select(CohortDraftAllowance)
            .where(CohortDraftAllowance.user_id == user_id, CohortDraftAllowance.period_start == period)
            .with_for_update()
        ).one()

        stale = now - timedelta(minutes=settings.COHORT_DRAFT_RESERVATION_TTL_MINUTES)
        db.execute(
            update(CohortDraftRequest)
            .where(
                col(CohortDraftRequest.user_id) == user_id,
                col(CohortDraftRequest.status) == CohortDraftStatus.RESERVED,
                col(CohortDraftRequest.created_at) < stale,
            )
            .values(status=CohortDraftStatus.RELEASED, model_calls=MAX_MODEL_CALLS, updated_at=now)
        )

        duplicate = db.exec(
            select(CohortDraftRequest.id).where(
                CohortDraftRequest.user_id == user_id, CohortDraftRequest.idempotency_key == idempotency_key
            )
        ).first()
        refusal: Refusal | None = None
        if duplicate is not None:
            refusal = Refusal.DUPLICATE
        else:
            recent = db.exec(
                select(func.count())
                .select_from(CohortDraftRequest)
                .where(
                    col(CohortDraftRequest.user_id) == user_id,
                    col(CohortDraftRequest.created_at) > now - timedelta(minutes=1),
                )
            ).one()
            used = db.exec(
                select(func.count()).select_from(CohortDraftRequest).where(_counts_against_allowance(user_id, now))
            ).one()
            calls = db.exec(
                select(
                    func.coalesce(
                        func.sum(
                            case(
                                (col(CohortDraftRequest.status) == CohortDraftStatus.RESERVED, MAX_MODEL_CALLS),
                                else_=col(CohortDraftRequest.model_calls),
                            )
                        ),
                        0,
                    )
                ).where(
                    col(CohortDraftRequest.user_id) == user_id,
                    col(CohortDraftRequest.period_start) == period,
                )
            ).one()
            if recent >= settings.COHORT_DRAFT_RATE_LIMIT_PER_MINUTE:
                refusal = Refusal.RATE_LIMITED
            elif calls + MAX_MODEL_CALLS > settings.COHORT_DRAFT_MONTHLY_REQUEST_CAP:
                refusal = Refusal.REQUEST_CAP
            elif used >= settings.COHORT_DRAFT_MONTHLY_ALLOWANCE:
                refusal = Refusal.EXHAUSTED
        if refusal is not None:
            db.commit()
            return refusal

        request = CohortDraftRequest(
            user_id=user_id,
            project_id=project_id,
            idempotency_key=idempotency_key,
            period_start=period,
            status=CohortDraftStatus.RESERVED,
            created_at=now,
            updated_at=now,
        )
        db.add(request)
        db.commit()
        return request.id
    except Exception:
        db.rollback()
        raise


def finish(db: Session, reservation_id: UUID, consumed: bool, model_calls: int, now: datetime | None = None) -> bool:
    """Consume the reservation for a validated draft, or release it for anything else, recording its model calls.

    Conditional on the reservation still being RESERVED, so a request is settled once. A reservation
    already released as abandoned stays released: that draft goes uncharged.

    Returns:
        bool: Whether the reservation was still open.
    """
    result = db.execute(
        update(CohortDraftRequest)
        .where(
            col(CohortDraftRequest.id) == reservation_id,
            col(CohortDraftRequest.status) == CohortDraftStatus.RESERVED,
        )
        .values(
            status=CohortDraftStatus.CONSUMED if consumed else CohortDraftStatus.RELEASED,
            model_calls=model_calls,
            updated_at=now or utcnow(),
        )
    )
    db.commit()
    return bool(result.rowcount)  # type: ignore[attr-defined]
