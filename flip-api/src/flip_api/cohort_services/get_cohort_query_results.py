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
import re
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import JSONResponse
from sqlmodel import Session, col, select

from flip_api.auth.access_manager import can_access_cohort_query
from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.db.models.main_models import Queries, QueryStats, Trust, TrustTask
from flip_api.domain.schemas.cohort import (
    CohortQueryFailureReason,
    CohortQueryTrustFailure,
    OmopCohortResultsResponse,
)
from flip_api.domain.schemas.status import TaskStatus, TaskType
from flip_api.utils.logger import logger

router = APIRouter(prefix="/cohort", tags=["cohort_services"])


_GENERIC_TRUST_FAILURE_MESSAGE = "Trust failed to process the query."

# Matches the data-access-api's exact phrasing for the cohort-size guard, regardless of whether
# it has been wrapped in additional layers (an ``HTTPException`` repr, a FastAPI ``{"detail":...}``
# envelope, or the trust handler's ``{"error":...}`` envelope) by the time it reaches the hub.
# Source: trust/data-access-api/data_access_api/routers/cohort.py
_INSUFFICIENT_COHORT_PATTERN = re.compile(
    r"Query returned too few records:\s*(\d+)\s*\(minimum required:\s*(\d+)\)",
    re.IGNORECASE,
)


def _classify_trust_failure(result: str | None) -> tuple[str, CohortQueryFailureReason]:
    """Pull a human-readable error message and a categorised reason out of ``TrustTask.result``.

    Trust handlers serialise failures as ``{"error": "<str>"}`` (and the stale-task recovery job
    uses the same shape). The ``<str>`` itself can be doubly wrapped — for cohort-size failures
    it's typically ``'400: {"detail":"Query returned too few records: 0 (minimum required: 5)"}'``
    because ``trust_api.utils.http.make_request`` packs the upstream FastAPI error body into an
    ``HTTPException``, then the trust handler stringifies that. Rather than peel each layer, we
    just regex-search for the canonical phrase, which is distinctive enough to be unambiguous.

    Args:
        result (str | None): The ``TrustTask.result`` column value.

    Returns:
        tuple[str, CohortQueryFailureReason]: A best-effort message suitable for display and a
        category the UI can branch on.
    """
    if not result:
        return _GENERIC_TRUST_FAILURE_MESSAGE, CohortQueryFailureReason.OTHER

    raw_message: str = result
    try:
        parsed = json.loads(result)
        if isinstance(parsed, dict) and parsed.get("error"):
            raw_message = str(parsed["error"])
    except (TypeError, ValueError):
        pass

    match = _INSUFFICIENT_COHORT_PATTERN.search(raw_message)
    if match:
        actual, minimum = match.group(1), match.group(2)
        friendly = (
            f"Cohort below minimum size threshold ({actual} record(s) "
            f"matched, {minimum} required)."
        )
        return friendly, CohortQueryFailureReason.INSUFFICIENT_COHORT

    return raw_message, CohortQueryFailureReason.OTHER


def _collect_trust_task_outcomes(
    db: Session, query_id: UUID
) -> tuple[list[CohortQueryTrustFailure], bool]:
    """Inspect cohort-query TrustTask rows for ``query_id`` and summarise their state.

    The cohort ``query_id`` is embedded in the JSON ``payload`` rather than being a first-class
    column, so we filter on a substring match for the UUID. UUIDs are unique enough that the
    additional ``task_type`` filter makes false positives effectively impossible.

    Args:
        db (Session): Active database session.
        query_id (UUID): The cohort query identifier to look up.

    Returns:
        tuple[list[CohortQueryTrustFailure], bool]:
            * Per-trust failures (FAILED tasks only) with a human-readable error message.
            * ``True`` if at least one task is still PENDING or IN_PROGRESS, else ``False``.
    """
    statement = (
        select(TrustTask, Trust.name)
        .join(Trust, col(Trust.id) == col(TrustTask.trust_id))
        .where(TrustTask.task_type == TaskType.COHORT_QUERY)
        .where(col(TrustTask.payload).contains(f'"{query_id}"'))
    )
    rows = db.exec(statement).all()

    failures: list[CohortQueryTrustFailure] = []
    has_pending = False
    for task, trust_name in rows:
        if task.status in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS):
            has_pending = True
        elif task.status == TaskStatus.FAILED:
            message, reason = _classify_trust_failure(task.result)
            # Build via ``model_validate`` rather than kwargs because the field declares an alias
            # (``trustName``) and mypy keys the call signature off the alias.
            failures.append(
                CohortQueryTrustFailure.model_validate({
                    "trust_name": trust_name,
                    "message": message,
                    "reason": reason,
                })
            )
    return failures, has_pending


# [#114] ✅
@router.get(
    "/{query_id}",
    response_model=OmopCohortResultsResponse,
    summary="Get cohort query results",
    description=(
        "Retrieve the aggregated results of a cohort query. Returns 202 while at least one "
        "trust is still processing the query, 200 once results are available or every trust "
        "has failed (in which case ``failures`` is populated and ``trustsResults`` is empty), "
        "and 404 only when the query id is unknown."
    ),
    responses={
        202: {"description": "Query exists but at least one trust is still processing it."},
        404: {"description": "No cohort query exists with the supplied id."},
    },
)
def get_cohort_query_results(
    query_id: UUID = Path(..., description="Unique identifier of the cohort query"),
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
) -> OmopCohortResultsResponse | JSONResponse:
    """
    Retrieve the aggregated results of a cohort query.

    Cohort queries are async: the hub queues tasks for all trusts, each trust runs the query
    against its OMOP database and posts results back. A trust may fail (e.g. "too few records"
    from data-access-api), in which case its TrustTask is marked FAILED but no row is added to
    QueryStats. To avoid leaving the UI in a permanent loading state when every trust fails,
    this endpoint also inspects TrustTask outcomes:

    * **200** — stats are populated; partial trust failures (if any) are included in
      ``failures`` so the UI can surface them alongside the successful aggregation.
    * **200** — no stats but every trust task is terminal (all failed). Returns an empty
      ``trustsResults`` with ``failures`` populated so the UI can show the per-trust errors
      instead of spinning forever.
    * **202** — query is known and at least one trust is still working on it.
    * **404** — query id is unknown (or access-layer denies it).

    Args:
        query_id (UUID): Unique identifier of the cohort query.
        db (Session): Database session dependency.
        user_id (UUID): ID of the user making the request, obtained from authentication.

    Returns:
        OmopCohortResultsResponse | JSONResponse: Results (200) or a pending marker (202).
    """
    try:
        logger.info(f"Checking access for user: {user_id} to cohort query: {query_id}")

        if not can_access_cohort_query(user_id, query_id, db):
            logger.info(f"Access denied: User {user_id} attempted to access cohort query {query_id}")
            raise HTTPException(
                status_code=403, detail=f"User with ID: {user_id} is denied access to this cohort query"
            )

        # Step 1: does the query exist at all? Needed so we can distinguish "unknown id"
        # (404) from "known but pending" (202). Without this, a freshly-submitted query
        # spuriously returns 404 during the window between POST /step/cohort and the first
        # trust's POST /cohort/results — which surfaces as a noisy error in the browser
        # console while the UI polls.
        query_exists = db.exec(select(Queries.id).where(Queries.id == query_id)).first()
        if not query_exists:
            logger.info(f"Cohort query not found for id: {query_id}")
            raise HTTPException(status_code=404, detail=f"Cohort query not found for id: {query_id}")

        # Step 2: results lookup. A single QueryStats row is expected per query.
        db_query_stats = db.exec(select(QueryStats.stats).where(QueryStats.query_id == query_id)).first()

        # Step 3: also collect TrustTask outcomes so we can (a) surface per-trust failures and
        # (b) recognise the "every trust failed, no QueryStats will ever be written" state.
        failures, has_pending_tasks = _collect_trust_task_outcomes(db, query_id)

        if not db_query_stats:
            if has_pending_tasks:
                logger.debug(f"Cohort query {query_id} is still pending trust responses")
                return JSONResponse(
                    status_code=202,
                    content={
                        "status": "pending",
                        "detail": "Results not yet available — trust responses pending",
                    },
                )
            logger.info(
                f"Cohort query {query_id} has no aggregated stats and every trust task is terminal; "
                f"returning {len(failures)} trust failure(s) to the caller"
            )
            return OmopCohortResultsResponse.model_validate({
                "record_count": 0,
                "trusts_results": [],
                "failures": [f.model_dump() for f in failures],
            })

        query_stats = OmopCohortResultsResponse.model_validate(json.loads(db_query_stats))
        # Surface partial failures alongside the successful aggregation so the UI can warn the
        # user that some trusts didn't contribute. ``failures`` is computed from TrustTask state
        # at request time, so it reflects the latest outcome even if QueryStats was written when
        # only some trusts had reported.
        if failures:
            query_stats.failures = failures
        logger.info("Results have been successfully obtained")
        return query_stats

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving cohort query results: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
