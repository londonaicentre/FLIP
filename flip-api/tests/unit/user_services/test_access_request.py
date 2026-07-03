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

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError
from fastapi import HTTPException, status
from fastapi.testclient import TestClient
from sqlmodel import Session

from flip_api.config import Settings
from flip_api.db.database import get_session
from flip_api.db.models.user_models import AccessRequest, AccessRequestStatus
from flip_api.domain.interfaces.shared import IAccessRequest, IUpdateAccessRequestStatus
from flip_api.main import app
from flip_api.user_services.access_request import (
    list_access_requests,
    request_access,
    update_access_request_status,
)


@pytest.fixture
def valid_access_request():
    """Valid access request data fixture."""
    return IAccessRequest(email="valid@email.com", full_name="Full Name", reason_for_access="reason for access")


@pytest.fixture
def mock_session():
    """A mock database session standing in for the injected SQLModel Session."""
    return MagicMock(spec=Session)


@pytest.fixture
def mocked_settings():
    mock = Settings(
        AWS_REGION="mock-region",
        AWS_SES_ADMIN_EMAIL_ADDRESS="admin@example.com",
        AWS_SES_SENDER_EMAIL_ADDRESS="sender@example.com",
    )
    with patch("flip_api.user_services.access_request.get_settings", return_value=mock):
        yield mock


def test_request_access_persists_and_emails(valid_access_request, mock_session, mocked_settings):
    """Happy path: the request is persisted, the admin email is sent, and email_notified is set."""
    with patch("flip_api.user_services.access_request.boto3.client") as mock_boto3_client:
        mock_ses_client = MagicMock()
        mock_boto3_client.return_value = mock_ses_client

        assert request_access(valid_access_request, mock_session) is None

        # Persisted first: an AccessRequest row was added and committed.
        assert mock_session.add.called
        persisted = mock_session.add.call_args_list[0].args[0]
        assert isinstance(persisted, AccessRequest)
        assert persisted.email == "valid@email.com"
        assert persisted.full_name == "Full Name"
        assert persisted.reason_for_access == "reason for access"
        assert persisted.status == AccessRequestStatus.PENDING
        assert mock_session.commit.called

        # Admin email dispatched via SES v2 with the expected template + payload.
        mock_boto3_client.assert_called_once_with("sesv2", region_name="mock-region")
        mock_ses_client.send_email.assert_called_once()
        call_args = mock_ses_client.send_email.call_args[1]
        assert call_args["FromEmailAddress"] == "sender@example.com"
        assert call_args["Destination"]["ToAddresses"] == ["admin@example.com"]
        assert call_args["Content"]["Template"]["TemplateName"] == "flip-access-request"
        assert "valid@email.com" in call_args["Content"]["Template"]["TemplateData"]
        assert "Full Name" in call_args["Content"]["Template"]["TemplateData"]
        assert "reason for access" in call_args["Content"]["Template"]["TemplateData"]

        # Flagged as notified once the email succeeds.
        assert persisted.email_notified is True


def test_request_access_persists_even_when_ses_fails(valid_access_request, mock_session, mocked_settings):
    """A SES failure is best-effort: it must NOT fail the submission nor lose the request (#699)."""
    with (
        patch("flip_api.user_services.access_request.boto3.client") as mock_boto3_client,
        patch("flip_api.user_services.access_request.logger") as mock_logger,
    ):
        mock_ses_client = MagicMock()
        mock_ses_client.send_email.side_effect = ClientError(
            {"Error": {"Code": "MessageRejected", "Message": "Email address is not verified"}}, "SendEmail"
        )
        mock_boto3_client.return_value = mock_ses_client

        # Does not raise despite the broken email backend.
        assert request_access(valid_access_request, mock_session) is None

        # The request was still persisted...
        persisted = mock_session.add.call_args_list[0].args[0]
        assert isinstance(persisted, AccessRequest)
        # ...but not flagged notified, and the failure was logged (not raised).
        assert persisted.email_notified is False
        mock_logger.error.assert_called()
        mock_session.rollback.assert_called()


def test_request_access_swallows_unexpected_notification_error(valid_access_request, mock_session, mocked_settings):
    """Any non-ClientError during notification is also swallowed after the request is persisted."""
    with (
        patch("flip_api.user_services.access_request.boto3.client", side_effect=Exception("boom")),
        patch("flip_api.user_services.access_request.logger") as mock_logger,
    ):
        assert request_access(valid_access_request, mock_session) is None

        # Persisted despite the notification path blowing up entirely.
        persisted = mock_session.add.call_args_list[0].args[0]
        assert isinstance(persisted, AccessRequest)
        mock_logger.error.assert_called()


def test_request_access_persist_failure_raises_500(valid_access_request, mock_session, mocked_settings):
    """If the request cannot be persisted the submission genuinely failed — surface a 500, skip the email."""
    mock_session.commit.side_effect = Exception("db down")
    with (
        patch("flip_api.user_services.access_request.boto3.client") as mock_boto3_client,
        patch("flip_api.user_services.access_request.logger"),
    ):
        with pytest.raises(HTTPException) as exc_info:
            request_access(valid_access_request, mock_session)

        assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        mock_session.rollback.assert_called()
        # No email is attempted once persistence fails.
        mock_boto3_client.assert_not_called()


def test_api_endpoint_returns_204(mocked_settings):
    """The endpoint accepts an unauthenticated camelCase body and returns 204."""
    mock_session = MagicMock(spec=Session)
    app.dependency_overrides[get_session] = lambda: mock_session
    try:
        with (
            patch("flip_api.user_services.access_request.boto3.client"),
            patch("flip_api.user_services.access_request.logger"),
        ):
            client = TestClient(app)
            # Note camelCase to mimic the UI call to the API.
            request_data = {
                "email": "test@example.com",
                "fullName": "Test User",
                "reasonForAccess": "Testing",
            }
            response = client.post("/api/users/access", json=request_data)
            assert response.status_code == status.HTTP_204_NO_CONTENT
    finally:
        app.dependency_overrides.pop(get_session, None)


@pytest.fixture
def mock_request():
    """A minimal request stand-in exposing empty pagination query params."""
    request = MagicMock()
    request.query_params = {}
    return request


def test_list_access_requests_requires_permission(mock_request, mock_session):
    """Listing is gated on CAN_MANAGE_USERS."""
    with patch("flip_api.user_services.access_request.has_permissions", return_value=False):
        with pytest.raises(HTTPException) as exc_info:
            list_access_requests(mock_request, None, mock_session, uuid4())
        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN


def test_list_access_requests_returns_paginated(mock_request, mock_session):
    """A permitted caller gets the persisted requests wrapped in paginated data."""
    access_request = AccessRequest(email="a@b.com", full_name="A B", reason_for_access="please")
    count_result = MagicMock()
    count_result.one_or_none.return_value = 1
    data_result = MagicMock()
    data_result.all.return_value = [access_request]
    mock_session.exec.side_effect = [count_result, data_result]

    with patch("flip_api.user_services.access_request.has_permissions", return_value=True):
        result = list_access_requests(mock_request, None, mock_session, uuid4())

    assert result.total_records == 1
    assert result.page == 1
    assert len(result.data) == 1
    assert result.data[0].email == "a@b.com"
    assert result.data[0].status == AccessRequestStatus.PENDING


def test_update_access_request_status_requires_permission(mock_session):
    """Updating status is gated on CAN_MANAGE_USERS."""
    with patch("flip_api.user_services.access_request.has_permissions", return_value=False):
        with pytest.raises(HTTPException) as exc_info:
            update_access_request_status(
                uuid4(), IUpdateAccessRequestStatus(status=AccessRequestStatus.DISMISSED), mock_session, uuid4()
            )
        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN


def test_update_access_request_status_404_when_missing(mock_session):
    """Updating a non-existent request yields 404."""
    mock_session.get.return_value = None
    with patch("flip_api.user_services.access_request.has_permissions", return_value=True):
        with pytest.raises(HTTPException) as exc_info:
            update_access_request_status(
                uuid4(), IUpdateAccessRequestStatus(status=AccessRequestStatus.ENROLLED), mock_session, uuid4()
            )
        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND


def test_update_access_request_status_sets_status_and_handler(mock_session):
    """A successful update sets the new status and records the acting admin."""
    access_request = AccessRequest(email="a@b.com", full_name="A B", reason_for_access="please")
    mock_session.get.return_value = access_request
    admin_id = uuid4()

    with patch("flip_api.user_services.access_request.has_permissions", return_value=True):
        result = update_access_request_status(
            access_request.id, IUpdateAccessRequestStatus(status=AccessRequestStatus.ENROLLED), mock_session, admin_id
        )

    assert result is access_request
    assert access_request.status == AccessRequestStatus.ENROLLED
    assert access_request.handled_by_user_id == admin_id
    mock_session.commit.assert_called()
