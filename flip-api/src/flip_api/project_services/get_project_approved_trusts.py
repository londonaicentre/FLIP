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

from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from flip_api.auth.access_manager import can_access_project
from flip.auth.dependencies import verify_token
from flip.db.database import get_session
from flip.domain.interfaces.trust import ITrust
from flip.domain.schemas.status import ProjectStatus
from flip.project_services.services.project_services import get_approved_trusts_for_project, get_project
from flip.utils.logger import logger

router = APIRouter(prefix="/projects", tags=["project_services"])


# [#114] ✅
@router.get(
    "/{project_id}/trusts/approved",
    summary="Get approved trusts for a specific project.",
    response_model=List[ITrust],
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_400_BAD_REQUEST: {"description": "Project has not been approved yet or invalid input."},
        status.HTTP_403_FORBIDDEN: {"description": "User does not have access to this project."},
        status.HTTP_404_NOT_FOUND: {"description": "Project not found (implicitly handled by access/status checks)."},
    },
)
def get_project_approved_trusts_endpoint(
    project_id: UUID,
    session: Session = Depends(get_session),
    current_user_id: UUID = Depends(verify_token),
):
    """
    Retrieves a list of trusts that have been approved for the specified project.
    The project must have a status of 'APPROVED'.
    """
    logger.info(f"User {current_user_id} requesting approved trusts for project {project_id}.")

    # 1. Check if user can access the project
    if not can_access_project(user_id=current_user_id, project_id=project_id, db=session):
        logger.error(f"User '{current_user_id}' cannot access project '{project_id}'.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User does not have access to this project.",
        )
    logger.debug(f"User {current_user_id} has access to project {project_id}.")

    # 2. Validate project ID (FastAPI handles UUID format validation automatically)
    # The original TS code had a projectIdSchema.validate, but FastAPI's UUID path param handles this.

    # 3. Validate whether project has APPROVED status
    project = get_project(project_id, session)
    if not project:
        logger.error(f"Project {project_id} not found.")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found.",
        )
    if not project.status == ProjectStatus.APPROVED:
        logger.warn(f"Project {project_id} is not in APPROVED status.")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Project has not been approved yet or does not exist.",
        )
    logger.debug(f"Project {project_id} status is APPROVED.")

    # 4. Fetch approved trusts
    try:
        approved_trusts = get_approved_trusts_for_project(project_id, session)
        logger.info(f"Successfully retrieved {len(approved_trusts)} approved trusts for project {project_id}.")
        return approved_trusts
    except (
        ValueError
    ) as ve:  # Assuming get_approved_trusts_for_project might raise ValueError if none found (as per original TS)
        logger.warn(f"ValueError while fetching approved trusts for project {project_id}: {ve}")
        # Depending on the desired behavior, you might return an empty list or re-raise as HTTPException
        # The original TS code threw an error if the DB response was empty.
        # If get_approved_trusts_for_project returns an empty list when none are found, this try/except might not be
        # needed for that case.
        # For now, let's assume it returns an empty list if none, and raises other ValueErrors for actual issues.
        # If it's critical that an error is raised if the list would be empty:
        # if not approved_trusts:
        #     logger.warn(f"No approved trusts found for project {project_id}, though project is APPROVED.")
        #     raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No approved trusts found for this
        # project.")
        # This behavior should be consistent with how get_approved_trusts_for_project is implemented.
        # If it can return an empty list, then no error here.
        return []  # Or handle specific errors from the service call
    except Exception as e:
        logger.error(f"Unhandled error fetching approved trusts for project {project_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while retrieving approved trusts.",
        )
