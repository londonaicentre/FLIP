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

import boto3
import httpx
from botocore.exceptions import ClientError
from fastapi import APIRouter, Body, Depends, HTTPException, Path, Request, status
from sqlmodel import Session

from flip_api.auth.auth_utils import has_permissions
from flip_api.auth.dependencies import verify_token
from flip_api.config import get_settings
from flip_api.db.database import get_session
from flip_api.db.models.main_models import XNATImageStatus
from flip_api.db.models.user_models import PermissionRef
from flip_api.domain.interfaces.trust import (
    ICreatedImagingProject,
    ICreatedImagingUser,
    ICreateImagingProject,
    ISesTemplateData,
    ITrust,
)
from flip_api.private_services.project_images_helpers import insert_status
from flip_api.project_services.services.project_services import get_project, get_users_with_access
from flip_api.utils.cognito_helpers import get_cognito_users, get_user_pool_id
from flip_api.utils.constants import IMAGING_CREDENTIALS_TEMPLATE_NAME
from flip_api.utils.encryption import decrypt
from flip_api.utils.http import trust_ssl_context
from flip_api.utils.logger import logger

router = APIRouter(prefix="/trust", tags=["trusts_services"])


def send_xnat_login_to_new_users(
    imaging_project_id: UUID, imaging_project_name: str, trust: ITrust, created_users: List[ICreatedImagingUser]
):
    """
    Sends XNAT login credentials to new users in the imaging project.

    TODO consider also notifying existing users if they have been added to a new project.

    Args:
        imaging_project_id (UUID): ID of the project.
        imaging_project_name (str): Name of the project.
        trust (ITrust): Trust information.
        created_users (List[ICreatedImagingUser]): List of users with their credentials.

    Returns:
         None

    Raises:
        HTTPException: If there is an error sending the email.
    """
    ses_sender_email = get_settings().AWS_SES_SENDER_EMAIL_ADDRESS
    region_name = get_settings().AWS_REGION

    # Create SES client to send emails
    sesv2 = boto3.client("sesv2", region_name=region_name)

    for user in created_users:
        # Decrypt password
        decrypted_password = decrypt(user.encrypted_password)

        # Prepare email template data
        template_data = ISesTemplateData(
            trust_name=trust.name,
            project_name=imaging_project_name,
            project_id=imaging_project_id,
            username=user.username,
            password=decrypted_password,
        )

        try:
            logger.debug(f"Sending XNAT credentials via SES v2 to {user.email}")

            sesv2.send_email(
                FromEmailAddress=ses_sender_email,
                Destination={"ToAddresses": [user.email]},
                Content={
                    "Template": {
                        "TemplateName": IMAGING_CREDENTIALS_TEMPLATE_NAME,
                        "TemplateData": template_data.model_dump_json(),
                    }
                },
                # Optionally add: ConfigurationSetName="your-config-set"
            )

        except (ClientError, Exception) as e:
            logger.error(f"Failed to send imaging credentials to {user.email}. Error: {str(e)}")


# TODO [#114] This endpoint was not defined in the old repo, rather it was run as a step in a step function
# 'approveProject' (Approves project and starts images creation on trusts).
@router.post(
    "/projects/{project_id}/trusts/imaging",
    summary="Start imaging project creation",
    description="Initiates the creation of imaging project in the trust system",
    status_code=status.HTTP_201_CREATED,
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
    Initiates the creation of an imaging project in the trust system.

    Args:
        request (Request): FastAPI request object.
        project_id (str): ID of the project.
        trust (flip.domain.schemas.trusts_services.Trust): Trust information.
        db (Session): Database session.
        user_id (str): User ID from the request context.

    Returns:
        dict[str, str]: Success message if the imaging project creation process is initiated successfully.

    Raises:
        HTTPException: If the user does not have permission to start the imaging project creation, if the project is not
        found, if there is an error communicating with the trust, or if there is an error saving the imaging project
        status to the database.
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
        users_with_access = [user_id for user_id in get_users_with_access(project_id, db)]

        # Add owner of project to list of users
        users_with_access.append(project.owner_id)
        unique_users = {user_id for user_id in users_with_access}

        # Get Cognito users
        cognito_users = get_cognito_users(params={"UserPoolId": user_pool_id})

        # Create request data for trust
        request_data = ICreateImagingProject(
            project_id=project_id,
            trust_id=trust.id,
            project_name=project.name,
            query=project.query.query if project.query else None,
            users=[user for user in cognito_users if user.id in unique_users],
        )

        # Make request to trust to create imaging project
        endpoint = f"{trust.endpoint}/imaging"
        try:
            async with httpx.AsyncClient(timeout=30.0, verify=trust_ssl_context()) as client:
                response = await client.post(
                    url=endpoint,
                    json=request_data.model_dump(mode="json"),
                )
                logger.debug(f"Response from trust {trust.name}: {response.status_code} - {response.text}")
                response.raise_for_status()
                response_data = response.json()

        except Exception as e:
            error_message = f"Failed to communicate with trust {trust.name} at {endpoint}: {str(e)}"
            logger.error(error_message)
            raise HTTPException(status_code=500, detail=error_message)

        # Parse response data
        try:
            image_project = ICreatedImagingProject(
                imaging_project_id=response_data["ID"],
                name=response_data["name"],
                created_users=[
                    ICreatedImagingUser(
                        username=user["username"],
                        encrypted_password=user["encrypted_password"],
                        email=user["email"],
                    )
                    for user in response_data.get("created_users", [])
                ],
            )
        except KeyError as e:
            error_message = f"Invalid response format from trust {trust.name}: {str(e)}"
            logger.error(error_message)
            raise HTTPException(status_code=500, detail=error_message)

        logger.debug(f"Received imaging project response: {image_project}")

        # Send login credentials to newly created users
        if image_project.created_users:
            send_xnat_login_to_new_users(
                imaging_project_id=image_project.imaging_project_id,
                imaging_project_name=image_project.name,
                trust=trust,
                created_users=image_project.created_users,
            )

        # Save imaging project to database
        logger.debug(f"Saving imaging project {image_project.imaging_project_id} to database")
        insert_status(
            trust_id=trust.id,
            xnat_project_id=image_project.imaging_project_id,
            project_id=project_id,
            status=XNATImageStatus.CREATED,
            db=db,
            query_id=project.query.id if project.query else None,
        )

        # Return success response
        return {"success": "Imaging project creation started successfully"}

    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        error_message = f"An error occurred while starting project imaging creation: {str(e)}"
        logger.error(error_message)
        raise HTTPException(status_code=500, detail=error_message)
