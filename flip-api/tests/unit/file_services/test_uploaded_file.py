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

import json
import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi.exceptions import HTTPException

from flip_api.config import Settings
from flip.db.models.main_models import UploadedFiles
from flip.domain.schemas.file import BucketStatus, FileUploadStatus, ScannedFileInput
from flip.file_services.uploaded_file import process_scanned_file


@pytest.fixture
def mock_s3_client():
    """Mock S3Client for testing."""
    with patch("flip.file_services.uploaded_file.S3Client") as mock_client:
        s3_instance = MagicMock()
        mock_client.return_value = s3_instance
        # Mock head_object to return file metadata
        s3_instance.head_object.return_value = {"ContentLength": 1024, "ContentType": "text/plain"}
        yield s3_instance


@pytest.fixture
def mocked_settings():
    mock = Settings(
        SCANNED_MODEL_FILES_BUCKET="test-secure-bucket",
    )
    with patch("flip.file_services.uploaded_file.get_settings", return_value=mock):
        yield mock


@pytest.fixture
def clean_file_event():
    """Create a sample SNS event for a clean file."""
    model_id = str(uuid.uuid4())
    file_name = "test.txt"
    key = f"{model_id}/{file_name}"

    message = {"bucket": "test-bucket", "key": key, "status": BucketStatus.CLEAN.value}

    return ScannedFileInput(Records=[{"Sns": {"Message": json.dumps(message)}}])


@pytest.fixture
def infected_file_event():
    """Create a sample SNS event for an infected file."""
    model_id = str(uuid.uuid4())
    file_name = "infected.txt"
    key = f"{model_id}/{file_name}"

    message = {"bucket": "test-bucket", "key": key, "status": BucketStatus.INFECTED.value}

    return ScannedFileInput(Records=[{"Sns": {"Message": json.dumps(message)}}])


@pytest.mark.skip("Bypassing virus scanning and AWS SNS after model file upload for the demo.")
def test_process_clean_file_success(mock_s3_client, mocked_settings, clean_file_event):
    """Test successful processing of a clean file."""
    # Mock the database session
    mock_db = MagicMock()
    mock_db.exec.return_value.first.return_value = None  # File doesn't exist in DB yet

    # Call the function
    result = process_scanned_file(clean_file_event, mock_db)

    # Verify result is the original message
    message = json.loads(clean_file_event.Records[0]["Sns"]["Message"])
    assert result == message

    # Verify database operations
    mock_db.add.assert_called_once()
    mock_db.commit.assert_called_once()

    # Verify S3 operations
    key = message["key"]
    mock_s3_client.head_object.assert_called()
    mock_s3_client.copy_object.assert_called_once_with(message["bucket"], key, "test-secure-bucket", key)
    mock_s3_client.delete_object.assert_called_once_with(message["bucket"], key)


@pytest.mark.skip("Bypassing virus scanning and AWS SNS after model file upload for the demo.")
def test_process_existing_file_update(mock_s3_client, mocked_settings, clean_file_event):
    """Test updating an existing file in the database."""
    # Create existing file record
    message = json.loads(clean_file_event.Records[0]["Sns"]["Message"])
    key_parts = message["key"].split("/")
    model_id = key_parts[0]
    file_name = key_parts[1]

    existing_file = UploadedFiles(
        name=file_name, status=FileUploadStatus.SCANNING, size=0, type="unknown", model_id=model_id
    )

    # Mock the database session
    mock_db = MagicMock()
    mock_db.exec.return_value.first.return_value = existing_file

    # Call the function
    process_scanned_file(clean_file_event, mock_db)

    # Verify file was updated not added
    mock_db.add.assert_not_called()
    mock_db.commit.assert_called_once()

    # Verify file attributes were updated
    assert existing_file.status == FileUploadStatus.COMPLETED
    assert existing_file.size == 1024
    assert existing_file.type == "text/plain"


@pytest.mark.skip("Bypassing virus scanning and AWS SNS after model file upload for the demo.")
def test_process_infected_file(mock_s3_client, mocked_settings, infected_file_event):
    """Test handling of an infected file."""
    # Mock the database session
    mock_db = MagicMock()
    mock_db.exec.return_value.first.return_value = None

    # Call the function and expect an HTTPException
    with pytest.raises(HTTPException) as excinfo:
        process_scanned_file(infected_file_event, mock_db)

    # Verify the status code is 400 (Bad Request)
    assert excinfo.value.status_code == 400

    # Verify S3 delete was called to remove the infected file
    message = json.loads(infected_file_event.Records[0]["Sns"]["Message"])
    mock_s3_client.delete_object.assert_called_once_with(message["bucket"], message["key"])

    # Verify file wasn't copied
    mock_s3_client.copy_object.assert_not_called()


@pytest.mark.skip("Bypassing virus scanning and AWS SNS after model file upload for the demo.")
def test_s3_head_object_exception(mock_s3_client, mocked_settings, clean_file_event):
    """Test handling an exception when retrieving file metadata."""
    # Setup mock to raise an exception
    mock_s3_client.head_object.side_effect = Exception("S3 head_object error")

    # Mock the database session
    mock_db = MagicMock()

    # Call the function and expect an HTTPException
    with pytest.raises(HTTPException) as excinfo:
        process_scanned_file(clean_file_event, mock_db)

    # Verify the status code is 500 (Internal Server Error)
    assert excinfo.value.status_code == 500
    assert "Unable to retrieve the file's details" in str(excinfo.value.detail)


@pytest.mark.skip("Bypassing virus scanning and AWS SNS after model file upload for the demo.")
def test_s3_copy_exception(mock_s3_client, mocked_settings, clean_file_event):
    """Test handling an exception when copying file to secure bucket."""
    # Setup mock to raise an exception on copy_object
    mock_s3_client.copy_object.side_effect = Exception("S3 copy error")

    # Mock the database session
    mock_db = MagicMock()
    mock_db.exec.return_value.first.return_value = None

    # Call the function and expect an HTTPException
    with pytest.raises(HTTPException) as excinfo:
        process_scanned_file(clean_file_event, mock_db)

    # Verify the status code is 500 (Internal Server Error)
    assert excinfo.value.status_code == 500
    assert "Unable to copy scanned file in secure bucket" in str(excinfo.value.detail)


@pytest.mark.skip("Bypassing virus scanning and AWS SNS after model file upload for the demo.")
def test_invalid_model_id(mock_s3_client, mocked_settings):
    """Test handling an invalid model ID in the file key."""
    # Create event with empty model ID
    message = {
        "bucket": "test-bucket",
        "key": "/test.txt",  # Missing model ID
        "status": BucketStatus.CLEAN.value,
    }

    event = ScannedFileInput(Records=[{"Sns": {"Message": json.dumps(message)}}])

    # Mock the database session
    mock_db = MagicMock()

    # Call the function and expect an HTTPException
    with pytest.raises(HTTPException) as excinfo:
        process_scanned_file(event, mock_db)

    # Verify the status code is 400 (Bad Request)
    assert excinfo.value.status_code == 400
    assert "valid model ID" in str(excinfo.value.detail)


@pytest.mark.skip("Bypassing virus scanning and AWS SNS after model file upload for the demo.")
def test_s3_verify_exception(mock_s3_client, mocked_settings, clean_file_event):
    """Test handling an exception when verifying file in secure bucket."""
    # Setup mock to raise an exception on second head_object call
    mock_s3_client.head_object.side_effect = [
        {"ContentLength": 1024, "ContentType": "text/plain"},  # First call succeeds
        Exception("S3 verification error"),  # Second call fails
    ]

    # Mock the database session
    mock_db = MagicMock()
    mock_db.exec.return_value.first.return_value = None

    # Call the function and expect an HTTPException
    with pytest.raises(HTTPException) as excinfo:
        process_scanned_file(clean_file_event, mock_db)

    # Verify the status code is 500 (Internal Server Error)
    assert excinfo.value.status_code == 500
    assert "Unable to access scanned file in secure bucket" in str(excinfo.value.detail)
