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

import json
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Request, status
from sqlmodel import Session

from flip_api.auth.auth_utils import has_permissions
from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.db.models.main_models import TrustTask
from flip_api.db.models.user_models import PermissionRef
from flip_api.domain.interfaces.trust import (
    ICreateImagingProject,
    ITrust,
)
from flip_api.domain.schemas.status import TaskType
from flip_api.project_services.services.project_services import get_project, get_users_with_access
from flip_api.utils.cognito_helpers import get_cognito_users, get_user_pool_id
from flip_api.utils.logger import logger

router = APIRouter(prefix="/trust", tags=["trusts_services"])


# TODO [#114] This endpoint was not defined in the old repo, rather it was run as a step in a step function
# 'approveProject' (Approves project and starts images creation on trusts).
@router.post(
    "/projects/{project_id}/trusts/imaging",
    summary="Start imaging project creation",
    description="Queues imaging project creation as a task for the trust to pick up via polling.",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=dict[str, str],
)
async def start_project_imaging_creation(
    request: Request,
    project_id: UUID = Path(..., description="ID of the project"),
    trust: ITrust = Body(..., description="Trust information"),
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
) -> dict[str, str]:
    """
    Queues imaging project creation as a task for the trust.

    Instead of making a direct HTTP call to the trust, this creates a TrustTask
    that the trust will pick up during its next polling cycle.

    Args:
        request (Request): FastAPI request object.
        project_id (UUID): ID of the project.
        trust (ITrust): Trust information.
        db (Session): Database session.
        user_id (UUID): User ID from the request context.

    Returns:
        dict[str, str]: Success message indicating the task has been queued.
    """
    try:
        # Permissions check
        if not has_permissions(user_id, [PermissionRef.CAN_APPROVE_PROJECTS], db):
            raise HTTPException(
                status_code=403,
                detail=f"User with ID: {user_id} was unable to start XNAT project creation",
            )

        # Get project details
        project = get_project(project_id, db)
        if not project:
            error_message = f"Central Hub project with {project_id=} not found. Unable to start XNAT project creation"
            logger.error(error_message)
            raise HTTPException(status_code=404, detail=error_message)

        # Get project users
        user_pool_id = get_user_pool_id(request)
        users_with_access = [uid for uid in get_users_with_access(project_id, db)]

        # Add owner of project to list of users
        users_with_access.append(project.owner_id)
        unique_users = {uid for uid in users_with_access}

        # Get Cognito users
        cognito_users = get_cognito_users(params={"UserPoolId": user_pool_id})

        # Create request data for trust
        request_data = ICreateImagingProject(
            project_id=project_id,
            trust_id=trust.id,
            project_name=project.name,
            query=project.query.query if project.query else None,
            users=[user for user in cognito_users if user.id in unique_users],
            dicom_to_nifti=project.dicom_to_nifti,
        )

        # Queue task for trust (instead of direct HTTP call)
        task = TrustTask(
            trust_id=trust.id,
            task_type=TaskType.CREATE_IMAGING,
            payload=json.dumps(request_data.model_dump(mode="json"), default=str),
        )
        db.add(task)
        db.commit()

        logger.info(f"Queued imaging creation task for trust {trust.name}, project {project_id}")
        return {"success": "Imaging project creation task queued successfully"}

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"An error occurred while queuing project imaging creation: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")
