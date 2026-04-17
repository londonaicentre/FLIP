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

"""Post-processing for completed CREATE_IMAGING tasks: persist status and email notifications."""

import json
from uuid import UUID

import boto3
from sqlmodel import Session, col, select

from flip_api.config import get_settings
from flip_api.db.models.main_models import Queries, Trust, TrustTask, XNATImageStatus, XNATProjectStatus
from flip_api.domain.interfaces.trust import (
    ICreatedImagingProject,
    ISesProjectAccessTemplateData,
    ISesTemplateData,
)
from flip_api.private_services.project_images_helpers import insert_status
from flip_api.utils.constants import IMAGING_CREDENTIALS_TEMPLATE_NAME, IMAGING_PROJECT_ACCESS_TEMPLATE_NAME
from flip_api.utils.encryption import decrypt
from flip_api.utils.logger import logger


def handle_imaging_task_completed(task: TrustTask, db: Session) -> None:
    """Post-process a successful CREATE_IMAGING task: persist status and send email notifications.

    Sends credential emails to newly created users, and project access notifications
    to existing users who were added to the project.

    Called after the task result has been committed to the database.
    Any exceptions are expected to be caught by the caller.

    Args:
        task (TrustTask): The completed CREATE_IMAGING task with result data.
        db (Session): Database session.

    Raises:
        ValueError: If the task has no result data.
    """
    if not task.result:
        raise ValueError(f"Task {task.id} has no result data")
    imaging_project = _parse_imaging_result(task.result)

    payload_data = json.loads(task.payload)
    project_id = UUID(payload_data["project_id"])

    # Persist imaging project status to database (idempotent: skip if already exists from a prior attempt)
    existing_status = db.exec(
        select(XNATProjectStatus)
        .where(XNATProjectStatus.trust_id == task.trust_id)
        .where(XNATProjectStatus.project_id == project_id)
        .where(XNATProjectStatus.retrieve_image_status == XNATImageStatus.CREATED)
    ).first()

    if not existing_status:
        query_id = _get_latest_query_id(project_id, db)
        insert_status(
            trust_id=task.trust_id,
            xnat_project_id=imaging_project.imaging_project_id,
            project_id=project_id,
            status=XNATImageStatus.CREATED,
            db=db,
            query_id=query_id,
        )
        logger.info(f"Saved imaging project status for '{imaging_project.name}'")
    else:
        logger.info(f"Status row already exists for trust {task.trust_id}, project {project_id} — skipping insert")

    # Skip emails if no users to notify
    if not imaging_project.created_users and not imaging_project.added_users:
        logger.info(f"No users to notify for imaging project '{imaging_project.name}'")
        return

    trust = db.exec(select(Trust).where(Trust.id == task.trust_id)).first()
    trust_name = trust.name if trust else "Unknown Trust"

    settings = get_settings()
    sesv2 = boto3.client("sesv2", region_name=settings.AWS_REGION)

    # Send credential emails to newly created users
    for user in imaging_project.created_users:
        try:
            decrypted_password = decrypt(user.encrypted_password)

            template_data = ISesTemplateData(
                trust_name=trust_name,
                project_name=imaging_project.name,
                project_id=project_id,
                username=user.username,
                password=decrypted_password,
            )

            sesv2.send_email(
                FromEmailAddress=settings.AWS_SES_SENDER_EMAIL_ADDRESS,
                Destination={"ToAddresses": [user.email]},
                Content={
                    "Template": {
                        "TemplateName": IMAGING_CREDENTIALS_TEMPLATE_NAME,
                        "TemplateData": json.dumps(template_data.model_dump(mode="json"), default=str),
                    }
                },
            )
            logger.info(f"Sent XNAT credentials email to {user.email} for project '{imaging_project.name}'")

        except Exception as e:
            logger.error(f"Failed to send credentials email to {user.email}: {e}")

    # Send project access notifications to existing users (no password)
    for added_user in imaging_project.added_users:
        try:
            access_template_data = ISesProjectAccessTemplateData(
                trust_name=trust_name,
                project_name=imaging_project.name,
                project_id=project_id,
                username=added_user.username,
            )

            sesv2.send_email(
                FromEmailAddress=settings.AWS_SES_SENDER_EMAIL_ADDRESS,
                Destination={"ToAddresses": [added_user.email]},
                Content={
                    "Template": {
                        "TemplateName": IMAGING_PROJECT_ACCESS_TEMPLATE_NAME,
                        "TemplateData": json.dumps(access_template_data.model_dump(mode="json"), default=str),
                    }
                },
            )
            logger.info(f"Sent project access email to {added_user.email} for project '{imaging_project.name}'")

        except Exception as e:
            logger.error(f"Failed to send project access email to {added_user.email}: {e}")


def _get_latest_query_id(project_id: UUID, db: Session) -> UUID | None:
    """Get the most recent query ID for a project, or None.

    Args:
        project_id (UUID): ID of the project.
        db (Session): Database session.

    Returns:
        UUID | None: The query ID, or None if no queries exist.
    """
    query = db.exec(
        select(Queries)
        .where(Queries.project_id == project_id)
        .order_by(col(Queries.created).desc())
        .limit(1)
    ).first()
    return query.id if query else None


def _parse_imaging_result(result_json: str) -> ICreatedImagingProject:
    """Parse the task result JSON into a structured imaging project response.

    Args:
        result_json (str): JSON string from the task result.

    Returns:
        ICreatedImagingProject: Structured imaging project response.

    Raises:
        json.JSONDecodeError: If result_json is not valid JSON.
        ValueError: If required fields (ID, name) are missing from the result.
    """
    data = json.loads(result_json)

    if "ID" not in data or "name" not in data:
        missing = [field for field in ("ID", "name") if field not in data]
        raise ValueError(f"Imaging result missing required fields: {missing}")

    return ICreatedImagingProject(
        imaging_project_id=data["ID"],
        name=data["name"],
        created_users=data.get("created_users", []),
        added_users=data.get("added_users", []),
    )
