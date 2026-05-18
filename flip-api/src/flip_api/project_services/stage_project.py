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

from flip_api.auth.access_manager import can_modify_project
from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.domain.interfaces.project import ProjectStatus
from flip_api.domain.schemas.projects import StageProjectRequest
from flip_api.project_services.services.project_services import (
    get_project,
    stage_project_service,
)
from flip_api.utils.logger import logger

router = APIRouter(prefix="/projects", tags=["project_services"])


# [#114] ✅
@router.post(
    "/{project_id}/stage",
    summary="Stage a project for approval at specified trusts.",
    status_code=status.HTTP_204_NO_CONTENT,  # Success returns no content
    response_model=None,
    responses={
        status.HTTP_400_BAD_REQUEST: {
            "description": "Invalid request (e.g., project not unstaged, no query, empty trusts)."
        },
        status.HTTP_403_FORBIDDEN: {"description": "User does not have permission to stage this project."},
        status.HTTP_404_NOT_FOUND: {"description": "Project not found."},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "An unexpected error occurred."},
    },
)
def stage_project_endpoint(
    project_id: UUID,
    payload: StageProjectRequest,
    session: Session = Depends(get_session),
    current_user_id: UUID = Depends(verify_token),
):
    """
    Stages a project for approval at the specified trusts.
    The project must be in 'UNSTAGED' status and have a valid cohort query.

    Args:
        project_id (UUID): The ID of the project to stage.
        payload (StageProjectRequest): The request payload containing the list of trust IDs to stage the project for.
        session (Session): The database session.
        current_user_id (UUID): The ID of the user making the request.

    Returns:
        None

    Raises:
        HTTPException: If the user does not have permission to stage the project, if the project does not exist, if the
                       project is not in 'UNSTAGED' status, if the project does not have a valid cohort query, or if
                       there is an unexpected error during staging.
    """
    logger.info(f"User {current_user_id} attempting to stage project {project_id} for trusts: {payload.trusts}")

    if not can_modify_project(user_id=current_user_id, project_id=project_id, db=session):
        logger.error(f"User {current_user_id} is not allowed to stage project {project_id}.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"User with ID: {current_user_id} is not allowed to stage this project.",
        )

    project_data = get_project(project_id, session)
    if not project_data:
        logger.error(f"Unable to find project with ID: {project_id} for staging.")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unable to find project with ID: {project_id}",
        )

    if project_data.status != ProjectStatus.UNSTAGED:
        logger.error(
            f"Project with ID: {project_id} is not '{ProjectStatus.UNSTAGED}' (actual: {project_data.status}) "
            "and cannot be staged."
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Project with ID: {project_id} is not '{ProjectStatus.UNSTAGED}' and cannot be staged.",
        )

    # Staging needs a cohort query that produced at least one trust result —
    # without that, there's nothing to stage against.
    if not project_data.query or not project_data.query.queried_trust_ids:
        error_msg = (
            f"Project with ID: {project_id} does not have a valid cohort query with trusts queried and cannot"
            " be staged."
        )
        logger.error(error_msg)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_msg,
        )
    logger.debug(
        f"Project {project_id} has query with {len(project_data.query.queried_trust_ids)} trusts queried."
    )

    # Trusts that joined the platform after the cohort query was submitted have
    # no QueryResult for it, so the user has no cohort count for them. Reject
    # staging requests that include any such trust — the UI already filters its
    # selector by `queriedTrustIds`, this guards direct API callers.
    queried_trust_ids = set(project_data.query.queried_trust_ids)
    unqueried_trust_ids = [tid for tid in payload.trusts if tid not in queried_trust_ids]
    if unqueried_trust_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Trusts {unqueried_trust_ids} were not part of the cohort query for project {project_id} "
                "and cannot be staged."
            ),
        )

    try:
        stage_project_service(
            project_id=project_id,
            trust_ids=payload.trusts,
            current_user_id=current_user_id,
            session=session,
        )
        logger.info(f"Project {project_id} successfully staged by user {current_user_id}.")

    except ValueError as ve:  # Catch specific business logic errors from services
        logger.error(f"ValueError during staging project {project_id}: {ve}", exc_info=True)
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(ve),
        )
    except Exception as e:
        logger.error(f"Unhandled error during staging project {project_id}: {e}", exc_info=True)
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while staging the project.",
        )
