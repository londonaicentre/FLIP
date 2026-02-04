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

from sqlmodel import Session

from flip_api.db.models.main_models import ProjectsAudit
from flip.domain.schemas.actions import ProjectAuditAction
from flip.utils.logger import logger


def audit_project_action(
    project_id: UUID, action: ProjectAuditAction, user_id: UUID, session: Session
) -> ProjectsAudit:
    """
    Insert a single audit log into the ProjectsAudit table.

    Args:
        project_id (UUID): The ID of the project being audited.
        action (ProjectAuditAction): The action performed on the project.
        user_id (UUID): The ID of the user performing the action.
        session (Session): SQLModel session.

    Returns:
        ProjectsAudit: The created ProjectsAudit entry.
    """
    logger.debug("Attempting to audit the action...")

    audit_record = ProjectsAudit(
        project_id=project_id,
        action=action,
        user_id=user_id,
    )

    session.add(audit_record)
    session.flush()  # Ensures audit_record gets an ID

    if not audit_record.id:
        raise ValueError("Unable to create audit for project")

    return audit_record
