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

"""Scheduled job to recover tasks stuck in IN_PROGRESS state.

If a trust picks up a task but crashes before reporting the result, the task
remains IN_PROGRESS forever. This module periodically resets such stale tasks
back to PENDING so they can be re-dispatched.
"""

from datetime import datetime, timedelta, timezone

from sqlmodel import Session, col, select

from flip_api.config import get_settings
from flip_api.db.database import engine
from flip_api.db.models.main_models import TrustTask
from flip_api.domain.schemas.status import TaskStatus, TaskType
from flip_api.utils.logger import logger


def recover_stale_tasks(db: Session) -> int:
    """Reset stale IN_PROGRESS tasks back to PENDING, or mark them FAILED if retries are exhausted.

    A task is considered stale if it has been IN_PROGRESS for longer than
    ``TASK_STALE_TIMEOUT_MINUTES`` without a result being reported.

    Tasks that have already been retried ``TASK_MAX_RETRIES`` times are marked
    as FAILED instead of being re-queued, preventing poison tasks from looping
    indefinitely.

    Args:
        db (Session): Database session.

    Returns:
        int: Number of tasks recovered (re-queued or failed).
    """
    settings = get_settings()
    timeout_minutes = settings.TASK_STALE_TIMEOUT_MINUTES
    max_retries = settings.TASK_MAX_RETRIES
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=timeout_minutes)

    statement = (
        select(TrustTask).where(TrustTask.status == TaskStatus.IN_PROGRESS).where(col(TrustTask.updated_at) < cutoff)
    )
    stale_tasks = db.exec(statement).all()

    if not stale_tasks:
        return 0

    now = datetime.now(timezone.utc)
    recovered = 0
    failed = 0
    for task in stale_tasks:
        if task.retry_count >= max_retries:
            logger.error(
                f"Task {task.id} (type={task.task_type}) exceeded max retries ({max_retries}), marking as FAILED"
            )
            task.status = TaskStatus.FAILED
            task.result = f'{{"error": "Exceeded maximum retries ({max_retries})"}}'
            task.updated_at = now
            failed += 1
        else:
            logger.warning(
                f"Recovering stale task {task.id} (type={task.task_type}, "
                f"retry_count={task.retry_count}, stuck since {task.updated_at})"
            )
            task.status = TaskStatus.PENDING
            task.retry_count += 1
            task.updated_at = now
            recovered += 1

    db.commit()
    if recovered:
        logger.info(f"Recovered {recovered} stale tasks back to PENDING")
    if failed:
        logger.info(f"Marked {failed} stale tasks as FAILED (max retries exceeded)")
    return recovered + failed


def retry_failed_post_processing(db: Session) -> int:
    """Retry post-processing for completed tasks that still need it.

    If a CREATE_IMAGING task completed but its post-processing (status persistence
    and email notifications) failed, this retries it.

    Args:
        db (Session): Database session.

    Returns:
        int: Number of tasks retried.
    """
    from flip_api.private_services.imaging_notifications import handle_imaging_task_completed

    statement = (
        select(TrustTask)
        .where(TrustTask.status == TaskStatus.COMPLETED)
        .where(TrustTask.task_type == TaskType.CREATE_IMAGING)
        .where(col(TrustTask.needs_post_processing).is_(True))
    )
    tasks = db.exec(statement).all()

    if not tasks:
        return 0

    retried = 0
    for task in tasks:
        try:
            handle_imaging_task_completed(task, db)
            task.needs_post_processing = False
            db.commit()
            retried += 1
            logger.info(f"Successfully retried post-processing for task {task.id}")
        except Exception as e:
            db.rollback()
            logger.error(f"Retry of post-processing failed for task {task.id}: {e}")

    return retried


def recover_stale_tasks_scheduled_task() -> None:
    """Scheduled task entry point for stale task recovery and post-processing retry."""
    logger.info("Running scheduled stale task recovery...")
    try:
        with Session(engine) as db:
            recover_stale_tasks(db)
            retry_failed_post_processing(db)
    except Exception as e:
        logger.error(f"Error in scheduled stale task recovery: {e}")
