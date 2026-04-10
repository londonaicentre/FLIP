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

from typing import Optional
from uuid import UUID

from sqlmodel import Session

from flip_api.db.models.main_models import (
    Projects,
)
from flip_api.domain.schemas.status import ProjectStatus
from flip_api.utils.logger import logger


def get_project_by_id(project_id: UUID, db: Session) -> Optional[Projects]:
    """
    Get project by ID from database, ignoring soft-deleted ones.

    Args:
        project_id (UUID): The ID of the project to retrieve.
        db (Session): The SQLModel session to use for database operations.

    Returns:
        Optional[Projects]: The project object if found, otherwise None.
    """
    logger.debug(f"Getting project with id: '{project_id}'")

    project = db.get(Projects, project_id)

    if not project:
        logger.debug(f"No project found with id: '{project_id}'")
        return None

    if project.deleted:
        logger.debug(f"Project '{project_id}' is marked as deleted")
        return None

    return project


def has_project_status(project_id: UUID, status: ProjectStatus, db: Session) -> bool:
    """
    Check if a project has the specified status

    Args:
        project_id (UUID): The ID of the project to check
        status (ProjectStatus): The status to check against the project
        db (Session): The database session to use for the query

    Returns:
        bool: True if the project has the specified status, False otherwise
    """
    logger.info(f"Checking project status for project ID: {project_id} with status: {status.value}")

    project = db.get(Projects, project_id)

    if not project:
        logger.error(f"Project {project_id} not found.")
        return False

    return project.status == status.value
