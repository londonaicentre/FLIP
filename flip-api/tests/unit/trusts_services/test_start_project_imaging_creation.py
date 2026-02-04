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

import logging
import uuid
from unittest import mock
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from botocore.exceptions import ClientError
from fastapi import HTTPException
from httpx import RequestError

from flip_api.domain.interfaces.project import IProjectQuery, IProjectResponse
from flip_api.domain.interfaces.trust import ITrust
from flip_api.domain.schemas.users import CognitoUser
from flip_api.trusts_services.start_project_imaging_creation import start_project_imaging_creation

# =============================================================================================
# Test data
# =============================================================================================
project_id = uuid.uuid4()
trust_id = uuid.uuid4()
trust_example = ITrust(id=trust_id, name="Example Trust", endpoint="http://example.com")

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


@pytest.fixture
def mock_trust_response():
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.raise_for_status = Mock()
    mock_response.json = Mock(
        return_value={
            "ID": str(uuid.uuid4()),  # simulate valid ID
            "name": "Test Imaging Project",
            "created_users": [
                {"email": user_email, "username": user_name, "encrypted_password": user_encrypted_password}
            ],
        }
    )
    return mock_response


@pytest.fixture
def mock_trust_client(mock_trust_response):
    mock_client = AsyncMock()
    mock_client.post.return_value = mock_trust_response

    mock_async_client = AsyncMock()
    mock_async_client.__aenter__.return_value = mock_client
    mock_async_client.__aexit__.return_value = None

    return mock_async_client


@pytest.fixture
def mock_boto3_ses_client():
    with mock.patch("flip_api.trusts_services.start_project_imaging_creation.boto3.client") as mock_ses:
        yield mock_ses


@pytest.fixture
def mock_decrypt():
    with mock.patch("flip_api.trusts_services.start_project_imaging_creation.decrypt") as mock_decrypt:
        mock_decrypt.return_value = "decrypted_password"
        yield mock_decrypt


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


# Test when imaging project returned from trust does not have 'ID', should raise an error
@pytest.mark.asyncio
async def test_project_missing_id(
    mock_request,
    mock_get_session,
    mock_has_permissions,
    mock_get_project,
    mock_get_user_pool_id,
    mock_get_users_with_access,
    mock_get_cognito_users,
    mock_trust_response,
    mock_trust_client,
):
    # Mock invalid response from trust (missing imaging_project_id)
    trust_response = mock_trust_response.json.return_value
    del trust_response["ID"]
    mock_trust_response.json.return_value = trust_response

    with patch("httpx.AsyncClient", return_value=mock_trust_client):
        with pytest.raises(HTTPException) as exc_info:
            await start_project_imaging_creation(
                request=mock_request,
                project_id=project_id,
                trust=trust_example,
                db=mock_get_session,
                user_id=user_id,
            )

    assert exc_info.value.status_code == 500
    assert "Invalid response format from trust Example Trust: 'ID'" in exc_info.value.detail


# Test case for successful imaging project creation
@pytest.mark.asyncio
async def test_successful_imaging_creation(
    mock_request,
    mock_get_session,
    mock_has_permissions,
    mock_get_project,
    mock_get_user_pool_id,
    mock_get_users_with_access,
    mock_get_cognito_users,
    mock_trust_response,
    mock_trust_client,
    mock_boto3_ses_client,
    mock_decrypt,
):
    # Mock SES client send_templated_email call
    mock_ses = mock_boto3_ses_client.return_value
    mock_ses.send_templated_email.return_value = {"MessageId": "12345"}

    # Patch the constructor so `async with httpx.AsyncClient(...) as client:` returns our mock_client
    with patch("httpx.AsyncClient", return_value=mock_trust_client):
        response = await start_project_imaging_creation(
            request=mock_request,
            project_id=project_id,
            trust=trust_example,
            db=mock_get_session,
            user_id=user_id,
        )

    assert response["success"] == "Imaging project creation started successfully"


# Test case for trust service failure (e.g., network error)
@pytest.mark.asyncio
async def test_trust_service_failure(
    mock_request,
    mock_get_session,
    mock_has_permissions,
    mock_get_project,
    mock_get_user_pool_id,
    mock_get_users_with_access,
    mock_get_cognito_users,
):
    async def mock_post(*args, **kwargs):
        raise RequestError("Network error")

    # Patch httpx.AsyncClient to use a client where post raises RequestError
    with patch("httpx.AsyncClient") as mock_client:
        mock_client_instance = mock_client.return_value.__aenter__.return_value
        mock_client_instance.post.side_effect = mock_post

        with pytest.raises(HTTPException) as exc_info:
            await start_project_imaging_creation(
                request=mock_request,
                project_id=project_id,
                trust=trust_example,
                db=mock_get_session,
            )

    assert exc_info.value.status_code == 500
    assert "Failed to communicate with trust" in exc_info.value.detail


# Test case for missing SES email config
@pytest.mark.asyncio
async def test_missing_ses_email_config(
    mock_request,
    mock_get_session,
    mock_has_permissions,
    mock_get_project,
    mock_get_user_pool_id,
    mock_get_users_with_access,
    mock_get_cognito_users,
    mock_trust_response,
    mock_trust_client,
    mock_boto3_ses_client,
    mock_decrypt,
    caplog,
):
    # Force SES send_templated_email to raise an error
    mock_boto3_ses_client.return_value.send_email.side_effect = Exception("SES email config missing")

    with patch("httpx.AsyncClient", return_value=mock_trust_client):
        with caplog.at_level(logging.ERROR):
            await start_project_imaging_creation(
                request=mock_request,
                project_id=project_id,
                trust=trust_example,
                db=mock_get_session,
            )

    # Assert the log message is present
    assert any(
        f"Failed to send imaging credentials to {user_email}. Error: SES email config missing" in message
        for message in caplog.messages
    )


# Test case for SES email client error
@pytest.mark.asyncio
async def test_ses_email_client_error(
    mock_request,
    mock_get_session,
    mock_has_permissions,
    mock_get_project,
    mock_get_user_pool_id,
    mock_get_users_with_access,
    mock_get_cognito_users,
    mock_trust_response,
    mock_trust_client,
    mock_boto3_ses_client,
    mock_decrypt,
    caplog,
):
    # Mock the ClientError in response from SES
    error_response = {"Error": {"Code": "MessageRejected", "Message": "Email address is not verified."}}
    operation_name = "SendEmail"
    mock_boto3_ses_client.return_value.send_email.side_effect = ClientError(error_response, operation_name)

    with patch("httpx.AsyncClient", return_value=mock_trust_client):
        with caplog.at_level(logging.ERROR):
            await start_project_imaging_creation(
                request=mock_request,
                project_id=project_id,
                trust=trust_example,
                db=mock_get_session,
            )

    assert any(
        "Failed to send imaging credentials" in message and "Email address is not verified." in message
        for message in caplog.messages
    )


# Test case for insert status error
@pytest.mark.asyncio
async def test_insert_status_error(
    mock_request,
    mock_get_session,
    mock_has_permissions,
    mock_get_project,
    mock_get_user_pool_id,
    mock_get_users_with_access,
    mock_get_cognito_users,
    mock_trust_response,
    mock_trust_client,
    mock_boto3_ses_client,
):
    # Mock SES client send_templated_email call
    mock_ses = mock_boto3_ses_client.return_value
    mock_ses.send_templated_email.return_value = {"MessageId": "12345"}

    # Patch the constructor so `async with httpx.AsyncClient(...) as client:` returns our mock_client
    with patch("httpx.AsyncClient", return_value=mock_trust_client):
        with patch(
            "flip_api.trusts_services.start_project_imaging_creation.insert_status",
            side_effect=Exception("Insert error"),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await start_project_imaging_creation(
                    request=mock_request,
                    project_id=project_id,
                    trust=trust_example,
                    db=mock_get_session,
                )

    assert exc_info.value.status_code == 500
    assert "An error occurred while starting project imaging creation" in exc_info.value.detail
