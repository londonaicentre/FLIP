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

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlmodel import Session

from flip_api.db.database import get_engine
from flip_api.domain.schemas.status import NetStatus
from flip_api.fl_services.services.fl_scheduler_service import (
    check_for_available_net,
    check_for_queued_jobs,
    log_queue_positions,
    prepare_and_start_training,
)
from flip_api.utils.logger import logger
from flip_api.utils.site_manager import is_deployment_mode_enabled


def _recover_stale_busy_schedulers(db: Session) -> int:
    """Reset all FLScheduler rows stuck in BUSY to AVAILABLE.

    Uses a single atomic UPDATE statement to avoid read-side races with
    check_for_queued_jobs (which uses with_for_update) and eliminates the
    N+1 query pattern of the previous row-by-row approach.

    BUSY schedulers with no associated job, or whose job has been deleted,
    are unrecoverable unless cleaned up here. This prevents a single crash
    from permanently starving a net of new training jobs.
    """
    # Raw column names: SQLModel `alias=` on the FLScheduler model only affects
    # Pydantic API serialisation, not the SQLAlchemy column name — the actual
    # DB column is `job_id` (the field name), not the `jobid` alias.
    stmt = text(
        """
        UPDATE fl_scheduler
        SET status = :available, job_id = NULL
        WHERE status = :busy
          AND (job_id IS NULL
               OR job_id NOT IN (SELECT id FROM fl_job
                                 WHERE status NOT IN (:completed, :deleted)))
        """
    )
    result = db.execute(
        stmt,
        {
            "available": NetStatus.AVAILABLE.value,
            "busy": NetStatus.BUSY.value,
            "completed": "COMPLETED",
            "deleted": "DELETED",
        },
    )
    recovered = result.rowcount  # type: ignore[attr-defined]
    if recovered:
        db.commit()
        logger.info("Recovered %d stale BUSY scheduler(s)", recovered)
    return recovered


def run_jobs_core(db: Session) -> None:
    """Core logic to run FL jobs, with stale-BUSY scheduler recovery.

    Resets any FLScheduler rows stuck in BUSY status (e.g. from a crashed
    previous job run) before attempting to pick an available net.
    """
    try:
        _recover_stale_busy_schedulers(db)

        # Deployment mode is the operator's quiesce gate: in-flight jobs finish and
        # free their nets, but nothing new is picked up until the mode is disabled.
        if is_deployment_mode_enabled(db):
            logger.info("Deployment mode enabled — pausing FL job pickup. 🚧")
            return

        # Step 1: Find an available net
        scheduler = check_for_available_net(db)

        if not scheduler or not scheduler.id:
            logger.info("No available nets, will check again soon... 🔃")
            return

        # Step 2: Get a queued job for the selected net
        job = check_for_queued_jobs(scheduler.id, db)

        if not job or not job.id:
            logger.info({
                "message": "No jobs waiting, will check again soon... 🔃",
                "net": scheduler.netId,
            })
            return

        # The pickup just advanced every remaining queued model one place.
        log_queue_positions(db)

        # Step 3: Prepare and start training
        logger.info({
            "message": "About to prepare & start training... 📦",
            "net": scheduler.netId,
            "job": job.id,
            "model": job.model_id,
        })

        prepare_and_start_training(job.model_id, job.id, job.trust_ids, db)

        logger.info({
            "message": "Training started successfully! 🚀",
            "net": scheduler.netId,
            "job": job.id,
            "model": job.model_id,
        })
        return

    except HTTPException:
        # Author-written 4xx messages (403/404/400) are intentional and safe;
        # only genuinely unexpected exceptions get a generic message below.
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An error occurred while running jobs"
        )


def run_jobs_scheduled_task() -> None:
    """
    Scheduled task to run jobs every minute.
    This function is called by the scheduler.

    Raises:
        HTTPException: If there is an error while running jobs.
    """
    logger.info("Running scheduled run_jobs execution... ⏰")
    try:
        with Session(get_engine()) as db:
            run_jobs_core(db)
    except HTTPException:
        # Author-written 4xx messages (403/404/400) are intentional and safe;
        # only genuinely unexpected exceptions get a generic message below.
        raise
    except Exception:
        error_message = "Error in scheduled run_jobs execution"
        logger.exception(error_message)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error_message)
