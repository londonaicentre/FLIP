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
from io import BytesIO
from unittest.mock import MagicMock, patch
from urllib.parse import quote

import pytest
from fastapi import status
from fastapi.exceptions import HTTPException
from fastapi.responses import StreamingResponse

from flip_api.config import Settings
from flip.file_services.download_file import download_file

bucket = "s3://test-secure-bucket"


@pytest.fixture
def mock_s3_client():
    """Mock S3Client for testing."""
    with patch("flip.file_services.download_file.S3Client") as mock_client:
        s3_instance = MagicMock()
        mock_client.return_value = s3_instance

        # Mock get_object to return file data
        s3_response = {"Body": BytesIO(b"test file content"), "ContentType": "text/plain"}
        s3_instance.get_object.return_value = s3_response

        yield s3_instance


@pytest.fixture
def mock_access_manager():
    """Mock can_access_model function."""
    with patch("flip.file_services.download_file.can_access_model") as mock_access:
        # Default to allowing access
        mock_access.return_value = True
        yield mock_access


@pytest.fixture
def mocked_settings():
    mock = Settings(
        SCANNED_MODEL_FILES_BUCKET=bucket,
    )
    with patch("flip.file_services.download_file.get_settings", return_value=mock):
        yield mock


@pytest.fixture
def sample_model_id():
    """Generate a sample model UUID."""
    return uuid.uuid4()


@pytest.fixture
def sample_user_id():
    """Generate a sample user ID."""
    return str(uuid.uuid4())


@pytest.fixture
def sample_file_name():
    """Generate a sample file name."""
    return "test_document.txt"


def test_download_file_success(
    mock_s3_client,
    mock_access_manager,
    mocked_settings,
    sample_model_id,
    sample_user_id,
    sample_file_name,
    mock_db_session,
):
    """Test successful file download."""
    # Call the function
    response = download_file(
        model_id=sample_model_id, file_name=sample_file_name, db=mock_db_session, user_id=sample_user_id
    )

    # Assert: response type + headers
    assert isinstance(response, StreamingResponse)
    assert response.media_type == "text/plain"

    expected_disposition = f"attachment; filename=\"{sample_file_name}\"; filename*=UTF-8''{quote(sample_file_name)}"
    assert response.headers["Content-Disposition"] == expected_disposition

    # Verify S3 calls
    mock_s3_client.get_object.assert_called_once_with(f"{bucket}/{sample_model_id}/{sample_file_name}")

    # Assert: access check called
    mock_access_manager.assert_called_once_with(sample_user_id, sample_model_id, mock_db_session)

    # Assert: DB check executed
    mock_db_session.exec.assert_called_once()


def test_download_file_access_denied(
    mock_access_manager, mocked_settings, sample_model_id, sample_user_id, sample_file_name, mock_db_session
):
    """Test when user doesn't have access to the model."""
    # Configure mock to deny access
    mock_access_manager.return_value = False

    # Call the function and expect an HTTPException
    with pytest.raises(HTTPException) as excinfo:
        download_file(model_id=sample_model_id, file_name=sample_file_name, db=mock_db_session, user_id=sample_user_id)

    # Verify the status code is 403 (Forbidden)
    assert excinfo.value.status_code == status.HTTP_403_FORBIDDEN
    assert "denied access" in str(excinfo.value.detail)


def test_download_file_not_found(
    mock_access_manager, mocked_settings, sample_model_id, sample_user_id, sample_file_name, mock_db_session
):
    """Test when file is not found in the database."""
    # Configure mock to return None (file not found)
    mock_db_session.exec.return_value.first.return_value = None

    # Call the function and expect an HTTPException
    with pytest.raises(HTTPException) as excinfo:
        download_file(model_id=sample_model_id, file_name=sample_file_name, db=mock_db_session, user_id=sample_user_id)

    # Verify the status code is 404 (Not Found)
    assert excinfo.value.status_code == status.HTTP_404_NOT_FOUND
    assert "not found" in str(excinfo.value.detail)


def test_download_file_s3_error(
    mock_s3_client,
    mock_access_manager,
    mocked_settings,
    sample_model_id,
    sample_user_id,
    sample_file_name,
    mock_db_session,
):
    """Test when there's an error getting the file from S3."""
    # Configure mock to raise an exception
    mock_s3_client.get_object.side_effect = Exception("S3 get_object error")

    # Call the function and expect an HTTPException
    with pytest.raises(HTTPException) as excinfo:
        download_file(model_id=sample_model_id, file_name=sample_file_name, db=mock_db_session, user_id=sample_user_id)

    # Verify the status code is 500 (Internal Server Error)
    assert excinfo.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert "Error downloading" in str(excinfo.value.detail)


def test_download_file_general_exception(
    mock_access_manager, mocked_settings, sample_model_id, sample_user_id, sample_file_name, mock_db_session
):
    """Test handling of a general unhandled exception."""
    # Configure access check to raise an unexpected exception
    mock_access_manager.side_effect = ValueError("Unexpected error")

    # Call the function and expect an HTTPException
    with pytest.raises(HTTPException) as excinfo:
        download_file(model_id=sample_model_id, file_name=sample_file_name, db=mock_db_session, user_id=sample_user_id)

    # Verify the status code is 500 (Internal Server Error)
    assert excinfo.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert "Internal server error" in str(excinfo.value.detail)
