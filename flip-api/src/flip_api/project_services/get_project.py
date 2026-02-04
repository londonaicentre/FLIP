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

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlmodel import Session

from flip_api.auth.access_manager import can_access_project
from flip.auth.dependencies import verify_token
from flip.config import get_settings
from flip.db.database import get_session
from flip.domain.interfaces.project import IReturnedProject
from flip.domain.schemas.status import ProjectStatus
from flip.project_services.services.project_services import (
    get_project,
    get_project_query,
    get_trusts_approval_status_for_project,
    get_users_with_access,
)
from flip.utils.cognito_helpers import (
    get_user_by_email_or_id,
)
from flip.utils.logger import logger

router = APIRouter(prefix="/projects", tags=["project_services"])


# [#114] ✅
@router.get(
    "/{project_id}",
    summary="Get detailed information for a specific project.",
    response_model=IReturnedProject,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_403_FORBIDDEN: {
            "description": "User does not have permission to access this project.",
        },
        status.HTTP_404_NOT_FOUND: {
            "description": "The project was not found.",
        },
        status.HTTP_400_BAD_REQUEST: {
            "description": "Invalid project ID format (though FastAPI handles this with 422 for UUID).",
        },
    },
)
def get_project_details_endpoint(
    request: Request,
    project_id: UUID,  # FastAPI handles UUID validation, returns 422 if invalid
    db: Session = Depends(get_session),
    current_user_id: UUID = Depends(verify_token),
) -> IReturnedProject:
    """
    Retrieves detailed information for a given project ID.
    This includes owner details, query information, approved trusts,
    and users with access, similar to the getProject.ts lambda.
    """
    logger.info(f"User {current_user_id} attempting to access details for project {project_id}")

    if not can_access_project(current_user_id, project_id, db):
        logger.warn(f"User {current_user_id} denied access to project {project_id}.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User does not have access to this project.",
        )

    project_db = get_project(project_id, db)
    if not project_db:
        logger.warn(f"Project with ID {project_id} not found.")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project with ID: {project_id} not found.",
        )

    logger.debug(f"Project {project_id} found: {project_db.name}")

    # Start getting extra details for this project
    if project_db.status != ProjectStatus.UNSTAGED:
        approved_trusts_for_project = get_trusts_approval_status_for_project(project_id, db)
        logger.debug(f"Fetched {len(approved_trusts_for_project)} approved trusts for project {project_id}")
    else:
        approved_trusts_for_project = []
        logger.debug(f"Project {project_id} is {project_db.status}, skipping approved trusts fetch.")

    users_with_access_info = get_users_with_access(project_id, db)
    logger.debug(f"Fetched {len(users_with_access_info)} users with access for project {project_id}")

    # Get cognito users
    # Assuming this call is intended to fetch the project owner's details.
    # The use of current_user_id here alongside project_db.owner_id (as email) is specific to its implementation.
    user_pool_id = get_settings().AWS_COGNITO_USER_POOL_ID
    owner_cognito_user = get_user_by_email_or_id(user_pool_id=user_pool_id, user_id=project_db.owner_id)

    if owner_cognito_user is None:
        logger.error(
            f"Owner Cognito user details could not be retrieved for project {project_id} "
            f"(based on project_db.owner_id: {project_db.owner_id}). Requesting user: {current_user_id}."
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve project owner's details.",
        )

    if owner_cognito_user.email is None:
        logger.error(
            f"Owner Cognito user (identified by project_db.owner_id: {project_db.owner_id}) for project {project_id} "
            f"has no email address in Cognito. Requesting user: {current_user_id}."
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Project owner's email address is missing.",
        )

    # At this point, owner_cognito_user.email is guaranteed to be a non-None string.
    # Pydantic will validate if it's a valid EmailStr format during IReturnedProject instantiation.
    resolved_owner_email = owner_cognito_user.email

    users_with_access_email = [
        get_user_by_email_or_id(
            user_pool_id=user_pool_id,
            user_id=user_id,
        ).email
        for user_id in users_with_access_info
    ]
    users_with_access_email += [resolved_owner_email]
    users_with_access_email = list(set(users_with_access_email))  # Remove duplicates
    users_with_access_email = list(filter(None, users_with_access_email))  # Remove any None values

    # Build object to return
    response_data = IReturnedProject(
        id=project_db.id,
        name=project_db.name,
        description=project_db.description,
        status=project_db.status,
        owner_email=resolved_owner_email,
        approved_trusts=approved_trusts_for_project,
        query=get_project_query(project_db),
        users=users_with_access_email,  # type: ignore # The list is cleaned of None values above
        creation_timestamp=project_db.creation_timestamp.isoformat(timespec="milliseconds"),
        owner_id=project_db.owner_id,
    )  # type: ignore[call-arg]

    logger.info(f"Successfully retrieved details for project {project_id}")
    return response_data
