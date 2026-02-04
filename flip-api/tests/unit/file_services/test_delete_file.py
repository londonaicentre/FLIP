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
from unittest.mock import patch

import pytest
from fastapi import HTTPException, status
from sqlmodel import Session

from flip_api.config import Settings
from flip.file_services.delete_file import (
    S3Client,
    delete_model_file,
)

# Common test data
model_id = uuid.uuid4()
file_name = "test_file.txt"
user_id = uuid.uuid4()
bucket_name = "s3://test_bucket/scanned_model_files"


@pytest.fixture
def mocked_settings():
    mock = Settings(
        SCANNED_MODEL_FILES_BUCKET=bucket_name,
    )
    with patch("flip.file_services.delete_file.get_settings", return_value=mock):
        yield mock


@patch("flip.file_services.delete_file.can_access_model", return_value=True)
def test_delete_model_file_success(session: Session, monkeypatch, mocked_settings):
    """Test successful file deletion from S3 and database."""
    # Mock S3 client
    s3_client_mock = mock.Mock(spec=S3Client)
    monkeypatch.setattr("flip.file_services.delete_file.S3Client", lambda: s3_client_mock)

    # Mock database operations
    db_mock = mock.Mock(spec=Session)
    db_mock.exec.return_value.first.return_value = mock.Mock()  # Simulate file found in DB

    # Call the function
    result = delete_model_file(model_id, file_name, db_mock, user_id)

    # Assertions
    s3_client_mock.delete_object.assert_called_once_with(f"{bucket_name}/{model_id}/{file_name}")
    db_mock.delete.assert_called_once()
    db_mock.commit.assert_called_once()
    assert result == {"message": f"File {file_name} deleted successfully from Model ID: {model_id}"}


def test_delete_model_file_no_access(session: Session, monkeypatch):
    """Test when the user does not have access to the model."""
    # Mock can_access_model to return False
    monkeypatch.setattr("flip.file_services.delete_file.can_access_model", lambda *args: False)

    # Call the function and assert HTTPException is raised
    with pytest.raises(HTTPException) as exc_info:
        delete_model_file(model_id, file_name, session, user_id)

    # Assertions
    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert str(user_id) in exc_info.value.detail


@patch("flip.file_services.delete_file.can_access_model", return_value=True)
def test_delete_model_file_s3_error(session: Session, monkeypatch):
    """Test when there is an error deleting from S3."""
    # Mock S3 client to raise an exception
    s3_client_mock = mock.Mock(spec=S3Client)
    s3_client_mock.delete_object.side_effect = Exception("S3 error")
    monkeypatch.setattr("flip.file_services.delete_file.S3Client", lambda: s3_client_mock)

    # Mock database operations
    db_mock = mock.Mock(spec=Session)
    db_mock.exec.return_value.first.return_value = mock.Mock()  # Simulate file found in DB

    # Mock environment variable
    monkeypatch.setenv("SCANNED_MODEL_FILES_BUCKET", bucket_name)

    # Call the function and assert HTTPException is raised
    with pytest.raises(HTTPException) as exc_info:
        delete_model_file(model_id, file_name, db_mock, user_id)

    # Assertions
    assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert "Error deleting" in exc_info.value.detail


def test_delete_model_file_general_exception(session: Session, monkeypatch):
    """Test when a general exception occurs."""
    # Mock ModelFileDelete to raise an exception
    monkeypatch.setattr(
        "flip.file_services.delete_file.ModelFileDelete",
        lambda *args, **kwargs: (_ for _ in ()).throw(Exception("General error")),
    )

    # Call the function and assert HTTPException is raised
    with pytest.raises(HTTPException) as exc_info:
        delete_model_file(model_id, file_name, session, user_id)

    # Assertions
    assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert "Internal server error" in exc_info.value.detail
