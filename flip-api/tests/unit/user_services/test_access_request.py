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

import pytest
from botocore.exceptions import ClientError
from fastapi import HTTPException, status
from fastapi.testclient import TestClient

from flip_api.config import Settings
from flip_api.domain.interfaces.shared import IAccessRequest
from flip_api.main import app
from flip_api.user_services.access_request import request_access


@pytest.fixture
def test_client():
    """Test client for the FastAPI app."""
    return TestClient(app)


@pytest.fixture
def valid_access_request():
    """Valid access request data fixture."""
    return IAccessRequest(email="valid@email.com", full_name="Full Name", reason_for_access="reason for access")


@pytest.fixture
def mocked_settings():
    mock = Settings(
        AWS_REGION="mock-region",
        AWS_SES_ADMIN_EMAIL_ADDRESS="admin@example.com",
        AWS_SES_SENDER_EMAIL_ADDRESS="sender@example.com",
    )
    with patch("flip_api.user_services.access_request.get_settings", return_value=mock):
        yield mock


def test_request_access_success(valid_access_request, mocked_settings):
    """Test successful access request email sending."""
    with (
        patch("flip_api.user_services.access_request.boto3.client") as mock_boto3_client,
    ):
        # Mock SES client
        mock_ses_client = MagicMock()
        mock_boto3_client.return_value = mock_ses_client

        # Call the function
        request_access(valid_access_request)

        # Assertions
        mock_boto3_client.assert_called_once_with("sesv2", region_name="mock-region")
        mock_ses_client.send_email.assert_called_once()

        # Verify template data
        call_args = mock_ses_client.send_email.call_args[1]
        assert call_args["FromEmailAddress"] == "sender@example.com"
        assert call_args["Destination"]["ToAddresses"] == ["admin@example.com"]
        assert call_args["Content"]["Template"]["TemplateName"] == "flip-access-request"
        assert "valid@email.com" in call_args["Content"]["Template"]["TemplateData"]
        assert "Full Name" in call_args["Content"]["Template"]["TemplateData"]
        assert "reason for access" in call_args["Content"]["Template"]["TemplateData"]


def test_ses_client_error(valid_access_request, mocked_settings):
    """Test when SES client throws an error."""
    with (
        patch("flip_api.user_services.access_request.boto3.client") as mock_boto3_client,
        patch("flip_api.user_services.access_request.logger") as mock_logger,
    ):
        # Mock SES client to raise an exception
        mock_ses_client = MagicMock()
        mock_ses_error = ClientError(
            {"Error": {"Code": "InvalidTemplate", "Message": "Template does not exist"}}, "SendTemplatedEmail"
        )
        mock_ses_client.send_email.side_effect = mock_ses_error
        mock_boto3_client.return_value = mock_ses_client

        # Call the function and check for exception
        with pytest.raises(HTTPException) as exc_info:
            request_access(valid_access_request)

        # Assertions
        assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
        assert "Error sending SES templated email" in exc_info.value.detail
        mock_logger.error.assert_called_once()


def test_unexpected_exception(valid_access_request):
    """Test handling of unexpected general exceptions."""
    with (
        patch("flip_api.user_services.access_request.boto3.client") as mock_boto3_client,
        patch("flip_api.user_services.access_request.logger") as mock_logger,
    ):
        # Mock boto3.client to raise an unexpected exception
        mock_boto3_client.side_effect = Exception("Unexpected error")

        # Call the function and check for exception
        with pytest.raises(HTTPException) as exc_info:
            request_access(valid_access_request)

        # Assertions
        assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "Internal server error" in exc_info.value.detail
        assert "Unexpected error" in mock_logger.error.call_args[0][0]


def test_api_endpoint(test_client):
    """Test the API endpoint."""
    with (
        patch("flip_api.user_services.access_request.boto3.client"),
        patch("flip_api.user_services.access_request.logger"),
    ):
        # Prepare request data
        # Note camelCase to mimic the UI call to the API
        request_data = {
            "email": "test@example.com",
            "fullName": "Test User",
            "reasonForAccess": "Testing",
        }

        # Call the endpoint
        response = test_client.post("/api/users/access", json=request_data)

        # Assertions
        assert response.status_code == status.HTTP_204_NO_CONTENT
