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

import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException, status
from psycopg2 import DatabaseError

from flip_api.db.models.main_models import Projects
from flip.db.models.user_models import PermissionRef
from flip.domain.schemas.projects import ProjectDetails
from flip.project_services.create_project import create_project_endpoint

# Common test data
TEST_USER_ID = uuid.uuid4()
TEST_PROJECT_ID = uuid.uuid4()
TEST_PROJECT_NAME = "Test Project Alpha"
TEST_PROJECT_DESCRIPTION = "A detailed description for Test Project Alpha."


@pytest.fixture
def mock_valid_payload():
    """Fixture for a valid project details payload."""
    payload = MagicMock(spec=ProjectDetails)
    payload.name = TEST_PROJECT_NAME
    payload.description = TEST_PROJECT_DESCRIPTION
    # Mock the dict() method to return what Projects(**payload.dict()) would expect
    payload_dict = {
        "name": TEST_PROJECT_NAME,
        "description": TEST_PROJECT_DESCRIPTION,
        "users": [],
    }
    payload = ProjectDetails(**payload_dict)
    return payload


@patch("flip.project_services.create_project.logger")
@patch("flip.project_services.create_project.has_permissions")
def test_create_project_endpoint_success(
    mock_has_permissions: MagicMock,
    mock_logger: MagicMock,
    mock_db_session: MagicMock,
    mock_valid_payload: ProjectDetails,
):
    """Test successful project creation."""
    # Arrange
    mock_has_permissions.return_value = True

    # Act
    result = create_project_endpoint(
        payload=mock_valid_payload,
        user_id=TEST_USER_ID,
        db=mock_db_session,
    )

    # Assert
    mock_logger.debug.assert_called_once_with(f"Attempting to create project by user: {TEST_USER_ID}")
    mock_has_permissions.assert_called_once_with(TEST_USER_ID, [PermissionRef.CAN_MANAGE_PROJECTS], mock_db_session)
    logged_message = mock_logger.info.call_args[0][0]
    assert "Project created successfully" in logged_message
    assert str(result.id) in logged_message


@patch("flip.project_services.create_project.logger")
@patch("flip.project_services.create_project.has_permissions")
def test_create_project_endpoint_no_permission(
    mock_has_permissions: MagicMock,
    mock_logger: MagicMock,
    mock_db_session: MagicMock,
    mock_valid_payload: ProjectDetails,
):
    """Test project creation when user lacks permission."""
    # Arrange
    mock_has_permissions.return_value = False

    # Act & Assert
    with pytest.raises(HTTPException) as exc_info:
        create_project_endpoint(
            payload=mock_valid_payload,
            user_id=TEST_USER_ID,
            db=mock_db_session,
        )

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc_info.value.detail == f"User with ID: {TEST_USER_ID} was unable to create this project"
    mock_logger.debug.assert_called_once_with(f"Attempting to create project by user: {TEST_USER_ID}")
    mock_has_permissions.assert_called_once_with(TEST_USER_ID, [PermissionRef.CAN_MANAGE_PROJECTS], mock_db_session)
    mock_logger.error.assert_called_once_with(f"User {TEST_USER_ID} does not have permission to create projects.")
    mock_db_session.add.assert_not_called()
    mock_db_session.commit.assert_not_called()


@patch("flip.project_services.create_project.logger")
@patch("flip.project_services.create_project.has_permissions")
def test_create_project_endpoint_missing_name(
    mock_has_permissions: MagicMock,
    mock_logger: MagicMock,
    mock_db_session: MagicMock,
    mock_valid_payload: ProjectDetails,  # Re-use and modify
):
    """Test project creation with missing project name."""
    # Arrange
    mock_has_permissions.return_value = True
    mock_valid_payload.name = ""  # Invalid name

    # Act & Assert
    with pytest.raises(HTTPException) as exc_info:
        create_project_endpoint(
            payload=mock_valid_payload,
            user_id=TEST_USER_ID,
            db=mock_db_session,
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "Project name is required."
    mock_logger.debug.assert_called_once_with(f"Attempting to create project by user: {TEST_USER_ID}")
    mock_has_permissions.assert_called_once_with(TEST_USER_ID, [PermissionRef.CAN_MANAGE_PROJECTS], mock_db_session)
    mock_logger.error.assert_called_once_with("Project name is required.")
    mock_db_session.add.assert_not_called()


@patch("flip.project_services.create_project.logger")
@patch("flip.project_services.create_project.has_permissions")
def test_create_project_endpoint_db_commit_fails(
    mock_has_permissions: MagicMock,
    mock_logger: MagicMock,
    mock_db_session: MagicMock,
    mock_valid_payload: ProjectDetails,
):
    """Test project creation when database commit fails."""
    # Arrange
    mock_has_permissions.return_value = True

    mock_new_project_instance = MagicMock(spec=Projects)
    mock_new_project_instance.id = TEST_PROJECT_ID  # Needed for potential logging if refresh was reached

    commit_error = DatabaseError("DB Commit Error")
    mock_db_session.commit.side_effect = commit_error

    # Act & Assert
    with pytest.raises(HTTPException) as exc_info:
        create_project_endpoint(
            payload=mock_valid_payload,
            user_id=TEST_USER_ID,
            db=mock_db_session,
        )

    assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert exc_info.value.detail == "An error occurred while creating the project."

    mock_logger.debug.assert_called_once_with(f"Attempting to create project by user: {TEST_USER_ID}")
    mock_has_permissions.assert_called_once_with(TEST_USER_ID, [PermissionRef.CAN_MANAGE_PROJECTS], mock_db_session)
    mock_db_session.commit.assert_called_once()
    mock_db_session.rollback.assert_called_once()
    mock_logger.error.assert_called_once_with(f"Error creating project: 500: Failed to create project: {commit_error}")
    mock_db_session.refresh.assert_not_called()
