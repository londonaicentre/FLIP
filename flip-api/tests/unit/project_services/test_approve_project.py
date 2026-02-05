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

from flip_api.db.models.main_models import Projects, Trust
from flip_api.db.models.user_models import PermissionRef
from flip_api.domain.schemas.projects import ApproveProjectBodyPayload
from flip_api.domain.schemas.status import ProjectStatus
from flip_api.project_services.approve_project import approve_project_endpoint

# Imports from the module to be tested
# Assuming sqlmodel.Session is used for type hinting or spec


# Common test data
TEST_PROJECT_ID = str(uuid.uuid4())
TEST_USER_ID = str(uuid.uuid4())
TEST_TRUST_IDS = [str(uuid.uuid4()), str(uuid.uuid4())]


@pytest.fixture
def mock_payload():
    payload = MagicMock(spec=ApproveProjectBodyPayload)
    payload.trusts = TEST_TRUST_IDS
    return payload


@pytest.fixture
def mock_staged_project():
    project = MagicMock(spec=Projects)
    project.id = TEST_PROJECT_ID
    project.status = ProjectStatus.STAGED
    return project


@patch("flip_api.project_services.approve_project.logger")
@patch("flip_api.project_services.approve_project.get_trusts")
@patch("flip_api.project_services.approve_project.approve_project", return_value=True)
@patch("flip_api.project_services.approve_project.has_permissions", return_value=True)
def test_approve_project_endpoint_success(
    mock_has_permissions,
    mock_approve_project,  # This is the approve_project function
    mock_get_trusts,  # This is the get_trusts function
    mock_logger,
    mock_db_session,  # Fixture
    mock_payload,  # Fixture
    mock_staged_project,  # Fixture
):
    # Arrange
    mock_db_session.get.return_value = mock_staged_project

    mock_trust_list = [MagicMock(spec=Trust), MagicMock(spec=Trust)]
    mock_get_trusts.return_value = mock_trust_list

    # Act
    result = approve_project_endpoint(
        project_id=TEST_PROJECT_ID,
        payload=mock_payload,
        user_id=TEST_USER_ID,
        db=mock_db_session,
    )

    # Assert
    mock_has_permissions.assert_called_once_with(TEST_USER_ID, [PermissionRef.CAN_APPROVE_PROJECTS], mock_db_session)

    mock_db_session.get.assert_called_once_with(Projects, TEST_PROJECT_ID)
    mock_approve_project.assert_called_once()
    mock_get_trusts.assert_called_once_with(mock_db_session, ids=mock_payload.trusts)

    assert result == mock_trust_list


@patch("flip_api.project_services.approve_project.logger")
@patch("flip_api.project_services.approve_project.has_permissions")
def test_approve_project_endpoint_no_permission(mock_has_permissions, mock_logger, mock_db_session, mock_payload):
    # Arrange
    mock_has_permissions.return_value = False

    # Act & Assert
    with pytest.raises(HTTPException) as exc_info:
        approve_project_endpoint(
            project_id=TEST_PROJECT_ID,
            payload=mock_payload,
            user_id=TEST_USER_ID,
            db=mock_db_session,
        )

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert f"User with ID: {TEST_USER_ID} was unable to approve this project" == exc_info.value.detail
    mock_has_permissions.assert_called_once_with(TEST_USER_ID, [PermissionRef.CAN_APPROVE_PROJECTS], mock_db_session)
    mock_logger.error.assert_called_once_with(
        f"User {TEST_USER_ID} does not have permission to approve project {TEST_PROJECT_ID}."
    )


@patch("flip_api.project_services.approve_project.logger")
@patch("flip_api.project_services.approve_project.has_permissions", return_value=True)
def test_approve_project_endpoint_project_not_found(
    mock_has_permissions,  # Patched with return_value=True
    mock_logger,
    mock_db_session,
    mock_payload,
):
    # Arrange
    mock_db_session.get.return_value = None  # Project not found

    # Act & Assert
    with pytest.raises(HTTPException, match="does not exist") as exc_info:
        approve_project_endpoint(
            project_id=TEST_PROJECT_ID,
            payload=mock_payload,
            user_id=TEST_USER_ID,
            db=mock_db_session,
        )

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
    assert f"Project ID: {str(TEST_PROJECT_ID)} does not exist" == exc_info.value.detail
    mock_db_session.get.assert_called_once_with(Projects, TEST_PROJECT_ID)
    mock_logger.error.assert_called_once_with(f"Project with ID {TEST_PROJECT_ID} not found for approval.")


@patch("flip_api.project_services.approve_project.logger")
@patch("flip_api.project_services.approve_project.has_permissions", return_value=True)
def test_approve_project_endpoint_project_not_staged(
    mock_has_permissions,
    mock_logger,
    mock_db_session,
    mock_payload,
    mock_staged_project,  # Use fixture but change status
):
    # Arrange
    mock_staged_project.status = "SOME_OTHER_STATUS"  # Not ProjectStatus.STAGED
    mock_db_session.get.return_value = mock_staged_project

    # Act & Assert
    with pytest.raises(HTTPException) as exc_info:
        approve_project_endpoint(
            project_id=TEST_PROJECT_ID,
            payload=mock_payload,
            user_id=TEST_USER_ID,
            db=mock_db_session,
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert "Unable to approve the project as it has not been staged" == exc_info.value.detail
    mock_logger.error.assert_called_once_with(f"Project {TEST_PROJECT_ID} is not in STAGED status, cannot approve.")


@patch("flip_api.project_services.approve_project.logger")
@patch("flip_api.project_services.approve_project.get_trusts")
@patch("flip_api.project_services.approve_project.approve_project")
@patch("flip_api.project_services.approve_project.has_permissions", return_value=True)
def test_approve_project_endpoint_commit_status_fails(
    mock_has_permissions,
    mock_approve_project,  # This is the approve_project function
    mock_get_trusts,  # This is the get_trusts function
    mock_logger,
    mock_db_session,
    mock_payload,
    mock_staged_project,
):
    # Arrange
    mock_db_session.get.return_value = mock_staged_project

    commit_error = Exception("DB Commit Error")
    mock_approve_project.side_effect = commit_error

    # Act & Assert
    with pytest.raises(HTTPException) as exc_info:
        approve_project_endpoint(
            project_id=TEST_PROJECT_ID,
            payload=mock_payload,
            user_id=TEST_USER_ID,
            db=mock_db_session,
        )

    assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert exc_info.value.detail == "DB Commit Error"


@patch("flip_api.project_services.approve_project.logger")
@patch("flip_api.project_services.approve_project.get_trusts")
@patch("flip_api.project_services.approve_project.approve_project", return_value=True)
@patch("flip_api.project_services.approve_project.has_permissions", return_value=True)
def test_approve_project_endpoint_fetch_trusts_exec_fails(
    mock_has_permissions,
    mock_approve_project,  # This is the approve_project function
    mock_get_trusts,  # This is the get_trusts function
    mock_logger,
    mock_db_session,
    mock_payload,
    mock_staged_project,
):
    # Arrange
    mock_db_session.get.return_value = mock_staged_project

    trust_exec_error = Exception("Trust exec Error")
    mock_get_trusts.side_effect = trust_exec_error

    # First commit for project status update is successful
    mock_db_session.commit.side_effect = [None, Exception("This second commit should not be reached")]

    # Act & Assert
    with pytest.raises(HTTPException) as exc_info:
        approve_project_endpoint(
            project_id=TEST_PROJECT_ID,
            payload=mock_payload,
            user_id=TEST_USER_ID,
            db=mock_db_session,
        )

    assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert str(trust_exec_error) in str(exc_info.value.detail)

    mock_logger.error.assert_called_with(
        f"Unhandled error during project approval for {TEST_PROJECT_ID}: {trust_exec_error!s}", exc_info=True
    )
