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

from flip_api.utils.get_secrets import get_secrets

# Import the function to test using relative import


@pytest.fixture
def mock_settings():
    """Mock settings for AWS region."""
    with patch("flip_api.utils.get_secrets.get_settings") as mock_get_settings:
        mock_get_settings.return_value.AWS_SECRET_NAME = "MY_SECRET"
        mock_get_settings.return_value.AWS_REGION = "test-west-1"
        yield mock_get_settings


@pytest.fixture
def mock_boto3_session():
    """Fixture to mock boto3 session and client"""
    with patch("boto3.session.Session") as mock_session:
        mock_client = MagicMock()
        mock_session.return_value.client.return_value = mock_client
        yield mock_session, mock_client


def test_get_secrets_success(mock_settings, mock_boto3_session):
    """Test successful retrieval of secrets"""
    _, mock_client = mock_boto3_session

    # Mock response from AWS Secrets Manager
    mock_response = {"SecretString": '{"key": "value"}'}
    mock_client.get_secret_value.return_value = mock_response

    # Call the function
    result = get_secrets()

    # Assert the result is what we expected
    assert result == {"key": "value"}

    # Verify the client was created with the correct parameters
    mock_boto3_session[0].return_value.client.assert_called_once_with(
        service_name="secretsmanager", region_name="test-west-1"
    )

    # Verify get_secret_value was called with the correct parameters
    mock_client.get_secret_value.assert_called_once()


def test_get_secrets_client_error(mock_boto3_session):
    """Test handling of ClientError exception"""
    _, mock_client = mock_boto3_session

    # Mock ClientError exception
    error = ClientError(
        {"Error": {"Code": "ResourceNotFoundException", "Message": "Secret not found"}},
        "get_secret_value",
    )
    mock_client.get_secret_value.side_effect = error

    # Call the function and expect an exception
    with pytest.raises(ClientError) as excinfo:
        get_secrets()

    # Verify the exception is the same one we raised
    assert excinfo.value == error


def test_get_secrets_missing_secret_string(mock_settings, mock_boto3_session):
    """Test error when SecretString key is missing from AWS response."""
    _, mock_client = mock_boto3_session
    mock_client.get_secret_value.return_value = {}

    with pytest.raises(ValueError, match="does not contain 'SecretString'"):
        get_secrets()


def test_get_secrets_invalid_json_secret_string(mock_settings, mock_boto3_session):
    """Test error when SecretString is not valid JSON."""
    _, mock_client = mock_boto3_session
    mock_client.get_secret_value.return_value = {"SecretString": "{'aes_key':'abc'}"}

    with pytest.raises(ValueError, match="has invalid JSON in SecretString"):
        get_secrets()


def test_get_secrets_non_object_json_secret_string(mock_settings, mock_boto3_session):
    """Test error when SecretString JSON is valid but not an object."""
    _, mock_client = mock_boto3_session
    mock_client.get_secret_value.return_value = {"SecretString": '["a", "b"]'}

    with pytest.raises(ValueError, match="must be a JSON object"):
        get_secrets()
