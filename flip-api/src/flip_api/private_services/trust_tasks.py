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

"""Trust ↔ hub private endpoints.

**Identity is the API key.** ``Depends(authenticate_trust)`` returns the resolved
``Trust`` row directly; routes use ``trust.id`` for joins and ``trust.name`` for
logs without an extra DB lookup. The trust-api never has to know its own name —
the hub tells it via the response body, and the trust-api self-checks against an
opt-in ``EXPECTED_TRUST_ID`` from its kit file.
"""

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from sqlmodel import Session, col, select

from flip_api.auth.access_manager import authenticate_trust
from flip_api.db.database import get_session
from flip_api.db.models.main_models import Trust, TrustTask
from flip_api.domain.schemas.private import TaskResultInput, TrustTaskResponse
from flip_api.domain.schemas.status import TaskStatus, TaskType
from flip_api.private_services.imaging_notifications import handle_imaging_task_completed
from flip_api.utils.encryption import encrypt, kid_for_trust
from flip_api.utils.logger import logger
from flip_api.utils.rate_limiter import limiter

router = APIRouter(tags=["private_services"])

# Max tasks returned per poll to prevent unbounded responses
PENDING_TASKS_LIMIT = 50


def _trust_identity(trust: Trust) -> dict[str, str]:
    """The identity block embedded in trust-facing response bodies.

    Lets the trust-api log which trust the hub resolved it as, and verify
    that resolution against an opt-in ``EXPECTED_TRUST_ID`` from the kit file.
    """
    return {"trust_id": str(trust.id), "trust_name": trust.name}


# ---------------------------------------------------------------------------
# Core handlers (key-only protocol). Each route function below is a thin
# wrapper that calls one of these so the deprecated `{trust_name}` shims share
# exactly the same code path.
# ---------------------------------------------------------------------------


def _get_pending_tasks(trust: Trust, db: Session) -> dict[str, object]:
    """Return up to ``PENDING_TASKS_LIMIT`` pending tasks for ``trust``, marking them in-progress.

    Args:
        trust (Trust): The authenticated trust.
        db (Session): Database session.

    Returns:
        dict[str, object]: ``{trust_id, trust_name, tasks: [TrustTaskResponse, ...]}``.

    Raises:
        HTTPException: 500 on any DB error.
    """
    logger.debug(f"Trust '{trust.name}' polling for pending tasks")
    try:
        # NOTE: no row-level locking — each trust is assumed to run a single poller replica.
        # If multiple replicas poll concurrently, add .with_for_update(skip_locked=True).
        tasks = db.exec(
            select(TrustTask)
            .where(TrustTask.trust_id == trust.id)
            .where(TrustTask.status == TaskStatus.PENDING)
            .order_by(col(TrustTask.created_at))
            .limit(PENDING_TASKS_LIMIT)
        ).all()

        if not tasks:
            logger.debug(f"No pending tasks for trust '{trust.name}'")
            return {**_trust_identity(trust), "tasks": []}

        now = datetime.now(timezone.utc)
        # Every task in this batch goes to one trust, so the whole batch is encrypted
        # under that trust's key when it has one. Resolved once rather than per task:
        # the lookup is keyring-only, and the answer cannot change within a batch.
        kid = kid_for_trust(trust_id=str(trust.id), trust_code=trust.code)
        response: list[TrustTaskResponse] = []
        for task in tasks:
            task.status = TaskStatus.IN_PROGRESS
            task.updated_at = now
            response.append(
                TrustTaskResponse(
                    id=task.id,
                    task_type=task.task_type,
                    payload=encrypt(task.payload, kid=kid),
                    created_at=task.created_at,
                )
            )
        db.commit()
        logger.info(f"Dispatched {len(response)} tasks to trust '{trust.name}'")
        return {**_trust_identity(trust), "tasks": response}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error fetching pending tasks for trust '{trust.name}': {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        )


def _submit_task_result(
    trust: Trust, task_id: UUID, task_result: TaskResultInput, db: Session
) -> dict[str, object]:
    """Record the outcome of a task that this trust owns.

    Args:
        trust (Trust): The authenticated trust.
        task_id (UUID): The task whose result is being submitted.
        task_result (TaskResultInput): The reported outcome.
        db (Session): Database session.

    Returns:
        dict[str, object]: ``{trust_id, trust_name, message}``.

    Raises:
        HTTPException: 404 if the task is missing, 403 if it belongs to a
            different trust, 409 if it is not currently ``IN_PROGRESS``, 500 on
            any other error.
    """
    logger.info(f"Received result for task {task_id} from trust '{trust.name}'")
    try:
        task = db.exec(select(TrustTask).where(TrustTask.id == task_id)).first()
        if task is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Task {task_id} not found",
            )
        if task.trust_id != trust.id:
            logger.warning(
                f"Trust '{trust.name}' attempted to submit result for task {task_id} "
                "which belongs to a different trust"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Task {task_id} does not belong to trust '{trust.name}'",
            )
        if task.status != TaskStatus.IN_PROGRESS:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Task {task_id} is not in progress (current status: {task.status})",
            )

        needs_post_processing = task_result.success and task.task_type == TaskType.CREATE_IMAGING

        task.status = TaskStatus.COMPLETED if task_result.success else TaskStatus.FAILED
        task.result = task_result.result
        task.updated_at = datetime.now(timezone.utc)
        task.needs_post_processing = needs_post_processing
        db.commit()

        # Post-process successful imaging project creation (persist status + send credential emails).
        if needs_post_processing:
            try:
                handle_imaging_task_completed(task, db)
                task.needs_post_processing = False
                db.commit()
            except Exception as post_err:
                logger.error(
                    f"Failed post-processing for imaging task {task_id}: {post_err}. "
                    "The stale task recovery job will retry this."
                )

        logger.info(f"Task {task_id} marked as {task.status}")
        return {**_trust_identity(trust), "message": f"Task {task_id} result recorded"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error submitting result for task {task_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        )


def _record_heartbeat(trust: Trust, db: Session) -> dict[str, object]:
    """Stamp the trust row with the current UTC time.

    Args:
        trust (Trust): The authenticated trust.
        db (Session): Database session.

    Returns:
        dict[str, object]: ``{trust_id, trust_name, message}``.

    Raises:
        HTTPException: 500 on any error.
    """
    logger.debug(f"Heartbeat received from trust '{trust.name}'")
    try:
        trust.last_heartbeat = datetime.now(timezone.utc)
        db.commit()
        return {**_trust_identity(trust), "message": "Heartbeat recorded"}
    except Exception as e:
        db.rollback()
        logger.error(f"Error recording heartbeat for trust '{trust.name}': {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        )


# ---------------------------------------------------------------------------
# Canonical routes — no `{trust_name}` segment. Identity is the API key.
# ---------------------------------------------------------------------------


@router.get(
    "/tasks/pending",
    summary="Get pending tasks for the authenticated trust",
    status_code=status.HTTP_200_OK,
)
@limiter.limit("30/minute")
def get_pending_tasks(
    request: Request,
    db: Session = Depends(get_session),
    authenticated_trust: Trust = Depends(authenticate_trust),
) -> dict[str, object]:
    """Poll for queued tasks for the authenticated trust.

    Args:
        request (Request): The FastAPI request, used by the rate limiter.
        db (Session): Database session.
        authenticated_trust (Trust): The trust resolved from the API key.

    Returns:
        dict[str, object]: ``{trust_id, trust_name, tasks: [...]}``. ``tasks`` is
        bounded by ``PENDING_TASKS_LIMIT``; each task's payload is encrypted for
        transport.
    """
    return _get_pending_tasks(authenticated_trust, db)


@router.post(
    "/tasks/{task_id}/result",
    summary="Submit task result",
    status_code=status.HTTP_200_OK,
)
@limiter.limit("60/minute")
def submit_task_result(
    request: Request,
    task_id: UUID,
    task_result: TaskResultInput = Body(...),
    db: Session = Depends(get_session),
    authenticated_trust: Trust = Depends(authenticate_trust),
) -> dict[str, object]:
    """Submit the result of a previously-dispatched task.

    Args:
        request (Request): The FastAPI request, used by the rate limiter.
        task_id (UUID): The task whose result is being submitted.
        task_result (TaskResultInput): The reported outcome.
        db (Session): Database session.
        authenticated_trust (Trust): The trust resolved from the API key.

    Returns:
        dict[str, object]: ``{trust_id, trust_name, message}``.
    """
    return _submit_task_result(authenticated_trust, task_id, task_result, db)


@router.post(
    "/trust/heartbeat",
    summary="Trust heartbeat",
    status_code=status.HTTP_200_OK,
)
@limiter.limit("30/minute")
def trust_heartbeat(
    request: Request,
    db: Session = Depends(get_session),
    authenticated_trust: Trust = Depends(authenticate_trust),
) -> dict[str, object]:
    """Record a heartbeat for the authenticated trust.

    Args:
        request (Request): The FastAPI request, used by the rate limiter.
        db (Session): Database session.
        authenticated_trust (Trust): The trust resolved from the API key.

    Returns:
        dict[str, object]: ``{trust_id, trust_name, message}``.
    """
    return _record_heartbeat(authenticated_trust, db)
