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
from unittest import mock
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from flip_api.domain.interfaces.project import IProjectQuery, IProjectResponse
from flip_api.domain.interfaces.trust import ITrust
from flip_api.domain.schemas.users import CognitoUser
from flip_api.trusts_services.start_project_imaging_creation import start_project_imaging_creation

# =============================================================================================
# Test data
# =============================================================================================
project_id = uuid.uuid4()
trust_id = uuid.uuid4()
trust_example = ITrust(id=trust_id, name="Example Trust")

# User data
user_id = uuid.uuid4()
user_name = "user one"
user_email = "user1@example.com"
user_encrypted_password = "encrypted_pw"
user_pool_id = uuid.uuid4()
# =============================================================================================


# Helper mock functions
@pytest.fixture
def mock_request():
    request = MagicMock()
    request.state.user.sub = user_id
    return request


@pytest.fixture
def mock_get_session():
    with mock.patch("flip_api.trusts_services.start_project_imaging_creation.get_session") as mock_get_session:
        mock_get_session.return_value = MagicMock()
        yield mock_get_session


@pytest.fixture
def mock_has_permissions():
    with mock.patch(
        "flip_api.trusts_services.start_project_imaging_creation.has_permissions"
    ) as mock_has_permissions:
        mock_has_permissions.return_value = True
        yield mock_has_permissions


@pytest.fixture
def mock_get_project():
    with mock.patch("flip_api.trusts_services.start_project_imaging_creation.get_project") as mock_get_project:
        query_id = uuid.uuid4()
        query = IProjectQuery(
            id=query_id, name="Test Query", query="SELECT * FROM table", trusts_queried=2, total_cohort=20
        )
        mock_get_project.return_value = IProjectResponse(
            id=project_id,
            name="Test Project",
            query=query,
            owner_id=user_id,
            dicom_to_nifti=True,
        )
        yield mock_get_project


@pytest.fixture
def mock_get_user_pool_id():
    with mock.patch(
        "flip_api.trusts_services.start_project_imaging_creation.get_user_pool_id"
    ) as mock_get_user_pool_id:
        mock_get_user_pool_id.return_value = user_pool_id
        yield mock_get_user_pool_id


@pytest.fixture
def mock_get_users_with_access():
    with mock.patch(
        "flip_api.trusts_services.start_project_imaging_creation.get_users_with_access"
    ) as mock_get_users_with_access:
        mock_get_users_with_access.return_value = [user_email]
        yield mock_get_users_with_access


@pytest.fixture
def mock_get_cognito_users():
    with mock.patch(
        "flip_api.trusts_services.start_project_imaging_creation.get_cognito_users"
    ) as mock_get_cognito_users:
        mock_get_cognito_users.return_value = [
            CognitoUser(id=user_id, email=user_email, is_disabled=False),
        ]
        yield mock_get_cognito_users


# Test case for permission failure
@pytest.mark.asyncio
async def test_permission_failure(mock_request, mock_get_session, mock_has_permissions):
    # Simulate permission denial
    mock_has_permissions.return_value = False

    with pytest.raises(HTTPException) as exc_info:
        await start_project_imaging_creation(
            request=mock_request,
            project_id=project_id,
            trust=trust_example,
            db=mock_get_session,
            user_id=user_id,
        )

    assert exc_info.value.status_code == 403
    assert f"User with ID: {user_id} was unable to start XNAT project creation" in exc_info.value.detail


# Test case for project not found
@pytest.mark.asyncio
async def test_project_not_found(mock_request, mock_get_session, mock_has_permissions, mock_get_project):
    # Simulate project not found
    mock_get_project.return_value = None

    with pytest.raises(HTTPException) as exc_info:
        await start_project_imaging_creation(
            request=mock_request,
            project_id=project_id,
            trust=trust_example,
            db=mock_get_session,
            user_id=user_id,
        )

    assert exc_info.value.status_code == 404
    assert (
        f"Central Hub project with {project_id=} not found. Unable to start XNAT project creation"
        in exc_info.value.detail
    )


# Test case for successful imaging project creation (now queues a task)
@pytest.mark.asyncio
async def test_successful_imaging_creation(
    mock_request,
    mock_get_session,
    mock_has_permissions,
    mock_get_project,
    mock_get_user_pool_id,
    mock_get_users_with_access,
    mock_get_cognito_users,
):
    response = await start_project_imaging_creation(
        request=mock_request,
        project_id=project_id,
        trust=trust_example,
        db=mock_get_session,
        user_id=user_id,
    )

    assert response["success"] == "Imaging project creation task queued successfully"
    # Verify task was added and committed
    mock_get_session.add.assert_called_once()
    mock_get_session.commit.assert_called_once()


# Test case for DB error during task creation
@pytest.mark.asyncio
async def test_db_error_during_task_creation(
    mock_request,
    mock_get_session,
    mock_has_permissions,
    mock_get_project,
    mock_get_user_pool_id,
    mock_get_users_with_access,
    mock_get_cognito_users,
):
    mock_get_session.add.side_effect = Exception("DB write failed")

    with pytest.raises(HTTPException) as exc_info:
        await start_project_imaging_creation(
            request=mock_request,
            project_id=project_id,
            trust=trust_example,
            db=mock_get_session,
            user_id=user_id,
        )

    assert exc_info.value.status_code == 500
    mock_get_session.rollback.assert_called_once()
    assert "Internal server error" in exc_info.value.detail


# Test case for dicom_to_nifti=False being included in the queued task payload
@pytest.mark.asyncio
async def test_dicom_to_nifti_false_forwarded_to_trust(
    mock_request,
    mock_get_session,
    mock_has_permissions,
    mock_get_project,
    mock_get_user_pool_id,
    mock_get_users_with_access,
    mock_get_cognito_users,
):
    import json

    # Override fixture to set dicom_to_nifti=False
    mock_get_project.return_value.dicom_to_nifti = False

    await start_project_imaging_creation(
        request=mock_request,
        project_id=project_id,
        trust=trust_example,
        db=mock_get_session,
        user_id=user_id,
    )

    # Verify the task payload includes dicom_to_nifti=False
    mock_get_session.add.assert_called_once()
    task = mock_get_session.add.call_args[0][0]
    payload = json.loads(task.payload)
    assert payload["dicom_to_nifti"] is False
