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

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from sqlmodel import Session, col, select

from flip_api.auth.access_manager import authenticate_trust, verify_trust_identity
from flip_api.db.database import get_session
from flip_api.db.models.main_models import Trust, TrustTask
from flip_api.domain.schemas.private import TaskResultInput, TrustTaskResponse
from flip_api.domain.schemas.status import TaskStatus, TaskType
from flip_api.private_services.imaging_notifications import handle_imaging_task_completed
from flip_api.utils.encryption import encrypt
from flip_api.utils.logger import logger
from flip_api.utils.rate_limiter import limiter

router = APIRouter(tags=["private_services"])

# Max tasks returned per poll to prevent unbounded responses
PENDING_TASKS_LIMIT = 50


def _get_trust_by_name(trust_name: str, db: Session) -> Trust:
    """Look up a trust by name, raising 404 if not found.

    Args:
        trust_name (str): Name of the trust to look up.
        db (Session): Database session.

    Returns:
        Trust: The trust object.

    Raises:
        HTTPException: 404 if the trust is not found.
    """
    trust = db.exec(select(Trust).where(Trust.name == trust_name)).first()
    if not trust:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Trust '{trust_name}' not found",
        )
    return trust


@router.get(
    "/tasks/{trust_name}/pending",
    summary="Get pending tasks for a trust",
    status_code=status.HTTP_200_OK,
    response_model=list[TrustTaskResponse],
)
@limiter.limit("12/minute")
def get_pending_tasks(
    request: Request,
    trust_name: str,
    db: Session = Depends(get_session),
    authenticated_trust: str = Depends(authenticate_trust),
) -> list[TrustTaskResponse]:
    """
    Returns pending tasks for the specified trust and marks them as in_progress.

    This endpoint is polled by trusts to pick up work dispatched by the central hub.
    """
    verify_trust_identity(trust_name, authenticated_trust)
    logger.debug(f"Trust '{trust_name}' polling for pending tasks")

    try:
        trust = _get_trust_by_name(trust_name, db)

        # NOTE: This query does not use row-level locking (e.g. with_for_update(skip_locked=True))
        # because each trust is assumed to run a single poller replica. If multiple replicas poll
        # concurrently, add .with_for_update(skip_locked=True) to prevent duplicate task execution.
        statement = (
            select(TrustTask)
            .where(TrustTask.trust_id == trust.id)
            .where(TrustTask.status == TaskStatus.PENDING)
            .order_by(col(TrustTask.created_at))
            .limit(PENDING_TASKS_LIMIT)
        )
        tasks = db.exec(statement).all()

        if not tasks:
            logger.debug(f"No pending tasks for trust '{trust_name}'")
            return []

        now = datetime.now(timezone.utc)
        response = []
        for task in tasks:
            task.status = TaskStatus.IN_PROGRESS
            task.updated_at = now
            response.append(
                TrustTaskResponse(
                    id=task.id,
                    task_type=task.task_type,
                    payload=encrypt(task.payload),
                    created_at=task.created_at,
                )
            )

        db.commit()
        logger.info(f"Dispatched {len(response)} tasks to trust '{trust_name}'")
        return response

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error fetching pending tasks for trust '{trust_name}': {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        )


@router.post(
    "/tasks/{trust_name}/{task_id}/result",
    summary="Submit task result",
    status_code=status.HTTP_200_OK,
    response_model=dict[str, str],
)
@limiter.limit("60/minute")
def submit_task_result(
    request: Request,
    trust_name: str,
    task_id: UUID,
    task_result: TaskResultInput = Body(...),
    db: Session = Depends(get_session),
    authenticated_trust: str = Depends(authenticate_trust),
) -> dict[str, str]:
    """
    Receives the result of a completed task from a trust.

    The trust_name path parameter is verified against the authenticated trust identity
    to prevent one trust from submitting results for another trust's tasks.
    """
    verify_trust_identity(trust_name, authenticated_trust)
    logger.info(f"Received result for task {task_id} from trust '{trust_name}'")

    try:
        trust = _get_trust_by_name(trust_name, db)

        task = db.exec(select(TrustTask).where(TrustTask.id == task_id)).first()
        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Task {task_id} not found",
            )

        if task.trust_id != trust.id:
            logger.warning(
                f"Trust '{trust_name}' attempted to submit result for task {task_id} which belongs to a different trust"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Task {task_id} does not belong to trust '{trust_name}'",
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

        # Post-process successful imaging project creation (persist status + send credential emails)
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
        return {"message": f"Task {task_id} result recorded"}

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error submitting result for task {task_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        )


@router.post(
    "/trust/{trust_name}/heartbeat",
    summary="Trust heartbeat",
    status_code=status.HTTP_200_OK,
    response_model=dict[str, str],
)
@limiter.limit("12/minute")
def trust_heartbeat(
    request: Request,
    trust_name: str,
    db: Session = Depends(get_session),
    authenticated_trust: str = Depends(authenticate_trust),
) -> dict[str, str]:
    """
    Receives a heartbeat from a trust, updating its last_heartbeat timestamp.
    This replaces the hub-initiated health check with a trust-initiated heartbeat.
    """
    verify_trust_identity(trust_name, authenticated_trust)
    logger.debug(f"Heartbeat received from trust '{trust_name}'")

    try:
        trust = _get_trust_by_name(trust_name, db)
        trust.last_heartbeat = datetime.now(timezone.utc)
        db.commit()

        return {"message": "Heartbeat recorded"}

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error recording heartbeat for trust '{trust_name}': {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        )
