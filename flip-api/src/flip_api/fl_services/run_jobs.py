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

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from flip_api.auth.dependencies import verify_token
from flip_api.db.database import engine, get_session
from flip_api.fl_services.services.fl_scheduler_service import (
    check_for_available_net,
    check_for_queued_jobs,
    prepare_and_start_training,
)
from flip_api.utils.logger import logger

router = APIRouter(prefix="/fl", tags=["fl_services"])


# [#114] ✅
@router.post("/jobs")
def run_jobs(db: Session = Depends(get_session), user_id: UUID = Depends(verify_token)) -> None:
    """
    Endpoint to run FL jobs. Calls the core logic to check for available nets, retrieve queued jobs, and start training.

    Args:
        db (Session): Database session.
        user_id (UUID): User ID from authentication.

    Returns:
        None
    """
    return run_jobs_core(db)


def run_jobs_core(db: Session) -> None:
    """
    Core logic to run FL jobs. This function is called by both the API endpoint and the scheduled task. It checks for
    available nets, retrieves queued jobs, and starts training.

    Args:
        db (Session): Database session.

    Returns:
        None

    Raises:
        HTTPException: If there is an error while running jobs.
    """
    try:
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

        # Step 3: Prepare and start training
        logger.info({
            "message": "About to prepare & start training... 📦",
            "net": scheduler.netId,
            "job": job.id,
            "model": job.model_id,
        })

        prepare_and_start_training(job.model_id, job.id, job.clients, "fl_jobs_scheduled_task", db)

        logger.info({
            "message": "Training started successfully! 🚀",
            "net": scheduler.netId,
            "job": job.id,
            "model": job.model_id,
        })
        return

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred while running jobs: {str(e)}"
        )


def run_jobs_scheduled_task():
    """
    Scheduled task to run jobs every minute.
    This function is called by the scheduler.
    """
    logger.info("Running scheduled run_jobs execution... ⏰")
    try:
        with Session(engine) as db:
            run_jobs_core(db)
    except Exception as e:
        error_message = f"Error in scheduled run_jobs execution: {str(e)}"
        logger.error(error_message)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error_message)
