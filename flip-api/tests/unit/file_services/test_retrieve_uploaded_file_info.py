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
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException, status

from flip_api.db.models.main_models import UploadedFiles
from flip_api.domain.schemas.file import IdList
from flip_api.file_services.retrieve_uploaded_file_info import get_uploaded_files_info, get_uploaded_files_info_post

# filepath: /app/src/flip/file_services/test_retrieve_uploaded_file_info.py


@pytest.fixture
def mock_logger():
    """Mock logger for testing."""
    with patch("flip_api.file_services.retrieve_uploaded_file_info.logger") as mock_logger:
        yield mock_logger


@pytest.fixture
def sample_file_ids():
    """Create sample file IDs for testing."""
    return [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]


@pytest.fixture
def sample_files(sample_file_ids):
    """Create sample uploaded files for testing."""
    model_id = uuid.uuid4()
    files = [
        UploadedFiles(
            id=sample_file_ids[0],
            name="test_file1.txt",
            size=1024,
            type="text/plain",
            status="PROCESSED",
            model_id=model_id,
            created=datetime.now(),
            modified=datetime.now(),
        ),
        UploadedFiles(
            id=sample_file_ids[1],
            name="test_file2.csv",
            size=2048,
            type="text/csv",
            status="SCANNING",
            model_id=model_id,
            created=datetime.now(),
            modified=datetime.now(),
        ),
    ]
    return files, sample_file_ids, model_id


@pytest.fixture
def mock_db_session(sample_files):
    """Mock database session with predefined response."""
    files, _, _ = sample_files

    class MockSession:
        def exec(self, query):
            class MockResult:
                def all(self):
                    return files

                def first(self):
                    return files[0] if files else None

            return MockResult()

    return MockSession()


@pytest.fixture
def empty_db_session():
    """Mock database session that returns empty results."""

    class MockSession:
        def exec(self, query):
            class MockResult:
                def all(self):
                    return []

                def first(self):
                    return None

            return MockResult()

    return MockSession()


class TestGetUploadedFilesInfo:
    """Tests for the GET endpoint get_uploaded_files_info."""

    def test_get_files_info_success(self, mock_db_session, sample_files):
        """Test successful retrieval of file information."""
        _, sample_file_ids, model_id = sample_files
        file_ids_str = ",".join(str(fid) for fid in sample_file_ids[:2])

        result = get_uploaded_files_info(file_ids=file_ids_str, db=mock_db_session, user_id="test-user-id")

        assert len(result) == 2
        assert result[0]["id"] == str(sample_file_ids[0])
        assert result[0]["name"] == "test_file1.txt"
        assert result[0]["size"] == 1024
        assert result[0]["type"] == "text/plain"
        assert result[0]["status"] == "PROCESSED"
        assert result[0]["modelId"] == str(model_id)
        assert "created" in result[0]
        assert "modified" in result[0]

        assert result[1]["id"] == str(sample_file_ids[1])
        assert result[1]["name"] == "test_file2.csv"
        assert result[1]["status"] == "SCANNING"

    def test_get_files_info_no_files_found(self, empty_db_session, sample_files):
        """Test handling of no files found."""
        _, sample_file_ids, _ = sample_files
        file_ids_str = ",".join(str(fid) for fid in sample_file_ids[:2])

        with pytest.raises(HTTPException) as exc_info:
            get_uploaded_files_info(file_ids=file_ids_str, db=empty_db_session, user_id="test-user-id")

        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
        assert "No files found" in exc_info.value.detail

    def test_get_files_info_invalid_uuid(self):
        """Test handling of invalid UUID format."""
        with pytest.raises(HTTPException) as exc_info:
            get_uploaded_files_info(file_ids="invalid-uuid,another-invalid", db=MagicMock(), user_id="test-user-id")

        assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
        assert "Invalid UUID format" in exc_info.value.detail

    def test_get_files_info_unexpected_error(self, mock_db_session, sample_files):
        """Test handling of unexpected errors."""
        _, sample_file_ids, _ = sample_files
        file_ids_str = ",".join(str(fid) for fid in sample_file_ids[:2])

        mock_db_session.exec = MagicMock(side_effect=Exception("Database connection error"))

        with pytest.raises(HTTPException) as exc_info:
            get_uploaded_files_info(file_ids=file_ids_str, db=mock_db_session, user_id="test-user-id")

        assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "Internal server error" in exc_info.value.detail


class TestGetUploadedFilesInfoPost:
    """Tests for the POST endpoint get_uploaded_files_info_post."""

    def test_post_files_info_success(self, mock_db_session, sample_files):
        """Test successful retrieval of file information using POST."""
        _, sample_file_ids, model_id = sample_files
        id_list = IdList(ids=sample_file_ids[:2])

        result = get_uploaded_files_info_post(id_list=id_list, db=mock_db_session, user_id="test-user-id")

        assert len(result) == 2
        assert result[0]["id"] == str(sample_file_ids[0])
        assert result[0]["name"] == "test_file1.txt"
        assert result[1]["id"] == str(sample_file_ids[1])
        assert result[1]["name"] == "test_file2.csv"

    def test_post_files_info_no_files_found(self, empty_db_session, sample_files):
        """Test handling of no files found using POST."""
        _, sample_file_ids, _ = sample_files
        id_list = IdList(ids=sample_file_ids[:2])

        with pytest.raises(HTTPException) as exc_info:
            get_uploaded_files_info_post(id_list=id_list, db=empty_db_session, user_id="test-user-id")

        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
        assert "No files found" in exc_info.value.detail

    def test_post_files_info_unexpected_error(self, mock_db_session, sample_files):
        """Test handling of unexpected errors using POST."""
        _, sample_file_ids, _ = sample_files
        id_list = IdList(ids=sample_file_ids[:2])

        mock_db_session.exec = MagicMock(side_effect=Exception("Database connection error"))

        with pytest.raises(HTTPException) as exc_info:
            get_uploaded_files_info_post(id_list=id_list, db=mock_db_session, user_id="test-user-id")

        assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "Internal server error" in exc_info.value.detail
