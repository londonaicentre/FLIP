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
from sqlmodel import Session

from flip_api.config import get_settings
from flip_api.db.database import engine
from flip_api.project_services.services.image_service import reimport_failed_studies
from flip_api.project_services.services.project_services import get_reimport_queries_service
from flip_api.utils.logger import logger


def reimport_imaging_project_studies(db: Session) -> None:
    """
    Checks for projects with unimported studies and reimports them if they are eligible (i.e. enough time has passed
    since the last reimport, and the max reimport count has not been reached).

    Args:
        db (Session): The database session.

    Returns:
        None

    Raises:
        HTTPException: If an error occurs during the reimport process.
    """
    project_reimport_rate = get_settings().PROJECT_REIMPORT_RATE
    max_reimport_count = get_settings().MAX_REIMPORT_COUNT

    reimport_queries = get_reimport_queries_service(max_reimport_count, db)
    logger.info(f"Reimport queries: {reimport_queries}")

    if not reimport_queries:
        # NOTE The old repo raised an HTTPException if no projects with unimported studies were found. This has been
        # relaxed to a warning log instead, as it is not an error condition if there are no projects.
        logger.warning("No projects with unimported studies were found.")
        return

    success = reimport_failed_studies(reimport_queries, db, project_reimport_rate)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while reimporting the studies.",
        )

    return


def reimport_imaging_project_studies_scheduled_task():
    """
    Scheduled task to reimport imaging project studies.
    This function is called by the scheduler.

    Raises:
        HTTPException: If an error occurs during the reimport process.
    """
    logger.info("🩻 Running scheduled reimport_imaging_project_studies execution...")
    try:
        with Session(engine) as db:
            reimport_imaging_project_studies(db)
    except Exception as e:
        error_message = f"Error in scheduled reimport_imaging_project_studies execution: {str(e)}"
        logger.error(error_message)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error_message)
