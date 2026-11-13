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

"""Cohort SQL drafting routes (FLIP#1556, Beta): draft SQL from a description, and read the allowance.

Drafting never runs SQL and never touches the project's cohort query: a draft only fills the editor,
and the existing Run action submits whatever the researcher keeps.
"""

import time
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlmodel import Session

from flip_api.auth.access_manager import can_modify_project
from flip_api.auth.dependencies import verify_token
from flip_api.cohort_services.drafting import ledger
from flip_api.cohort_services.drafting.draft_logger import logger
from flip_api.cohort_services.drafting.model import get_draft_model
from flip_api.cohort_services.drafting.prompt import PROMPT_VERSION
from flip_api.cohort_services.drafting.service import DraftOutcome, run_draft
from flip_api.config import get_settings
from flip_api.db.database import get_session
from flip_api.domain.schemas.cohort_draft import CohortDraftRequestBody, CohortDraftResponse, CohortDraftUsage
from flip_api.domain.schemas.status import ProjectStatus
from flip_api.utils.project_manager import get_project_by_id

router = APIRouter(prefix="/cohort", tags=["cohort_services"])

_REFUSAL_MESSAGES = {
    ledger.Refusal.EXHAUSTED: "You have used this month's drafts. Manual SQL and Run still work.",
    ledger.Refusal.RATE_LIMITED: "Too many draft requests in the last minute. Try again shortly.",
    ledger.Refusal.DUPLICATE: "This request was already received. Draft again to get a new draft.",
}
_ERROR_MESSAGE = "The assistant could not produce a valid draft. Try again, or write the SQL by hand."


def _usage(db: Session, user_id: UUID) -> CohortDraftUsage:
    usage = ledger.get_usage(db, user_id)
    return CohortDraftUsage(
        enabled=True,
        used=usage.used,
        limit=usage.limit,
        remaining=max(usage.limit - usage.used, 0),
        resets_at=usage.resets_at,
    )


@router.get(
    "/draft/usage",
    response_model=CohortDraftUsage,
    summary="Cohort SQL drafting allowance (Beta)",
    description="Whether drafting is enabled, and the caller's drafts used this UTC month.",
)
def get_cohort_draft_usage(
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
) -> CohortDraftUsage:
    """Return whether the assistant is enabled and the caller's monthly allowance.

    Args:
        db (Session): Database session.
        user_id (UUID): The authenticated caller.

    Returns:
        CohortDraftUsage: ``enabled=False`` with zero counts when the feature flag is off.
    """
    settings = get_settings()
    if not settings.COHORT_DRAFT_ENABLED:
        limit = settings.COHORT_DRAFT_MONTHLY_ALLOWANCE
        return CohortDraftUsage(
            enabled=False, used=0, limit=limit, remaining=limit, resets_at=ledger.next_reset(ledger.utcnow())
        )
    return _usage(db, user_id)


@router.post(
    "/draft",
    response_model=CohortDraftResponse,
    summary="Draft cohort SQL from a description (Beta)",
    description="Drafts and checks cohort SQL for an UNSTAGED project. Never runs SQL or changes the cohort.",
)
def draft_cohort_query(
    body: CohortDraftRequestBody,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", pattern=r"^[A-Za-z0-9_.:-]{1,128}$")],
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
) -> CohortDraftResponse:
    """Draft cohort SQL for a project the caller may edit.

    Args:
        body (CohortDraftRequestBody): Project, description and earlier clarification answers.
        idempotency_key (str): Client key for this request; a repeat is never charged twice.
        db (Session): Database session.
        user_id (UUID): The authenticated caller.

    Returns:
        CohortDraftResponse: The outcome and the caller's allowance after it.

    Raises:
        HTTPException: 404 when drafting is off or the project does not exist, 403 when the caller may
            not edit the project, 400 when it is staged or approved or the description is too long.
    """
    settings = get_settings()
    if not settings.COHORT_DRAFT_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    if len(body.description) > settings.COHORT_DRAFT_MAX_DESCRIPTION_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"Descriptions are limited to {settings.COHORT_DRAFT_MAX_DESCRIPTION_CHARS} characters.",
        )
    if not can_modify_project(user_id, body.project_id, db):
        raise HTTPException(status_code=403, detail=f"User with ID: {user_id} is not allowed to modify this project")
    project = get_project_by_id(body.project_id, db)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.status != ProjectStatus.UNSTAGED:
        raise HTTPException(status_code=400, detail="Drafting is only available before the project is staged")
    has_imaging = project.has_imaging

    reservation = ledger.reserve(db, user_id, body.project_id, idempotency_key)
    meta = {"user_id": str(user_id), "project_id": str(body.project_id), "prompt_version": PROMPT_VERSION}
    if isinstance(reservation, ledger.Refusal):
        logger.info({"event": "cohort_draft", "outcome": "refused", "reason": reservation.value, **meta})
        return CohortDraftResponse(
            status="error",
            error_code=reservation.value,
            message=_REFUSAL_MESSAGES[reservation],
            usage=_usage(db, user_id),
        )

    model = get_draft_model()
    started = time.monotonic()
    try:
        outcome = run_draft(model, body.description, [(c.question, c.answer) for c in body.clarifications], has_imaging)
    except Exception as e:
        # Never leave the reservation held, and never log the description or the model's output.
        logger.error({"event": "cohort_draft", "outcome": "exception", "error": type(e).__name__, **meta})
        outcome = DraftOutcome(status="error", error_code="unexpected")
    ledger.finish(db, reservation, consumed=outcome.status == "draft")

    logger.info(
        {
            "event": "cohort_draft",
            "outcome": outcome.status,
            "error_code": outcome.error_code,
            "refusal_category": outcome.refusal_category,
            "model": model.name,
            "has_imaging": has_imaging,
            "retries": outcome.retries,
            "input_tokens": outcome.input_tokens,
            "output_tokens": outcome.output_tokens,
            "latency_ms": round((time.monotonic() - started) * 1000),
            **meta,
        }
    )
    response = CohortDraftResponse(status=outcome.status, usage=_usage(db, user_id))
    if outcome.status == "draft":
        response.sql, response.explanation = outcome.sql, outcome.explanation
        response.assumptions, response.concepts_used = outcome.assumptions, outcome.concepts_used
    elif outcome.status == "clarification":
        response.question, response.options = outcome.question, outcome.options
    elif outcome.status == "decline":
        response.message = outcome.explanation
    else:
        # The code is internal detail (model_timeout, invalid_draft, ...): the client gets two buckets.
        response.error_code = "invalid_draft" if outcome.error_code == "invalid_draft" else "model_unavailable"
        response.message = _ERROR_MESSAGE
    return response
