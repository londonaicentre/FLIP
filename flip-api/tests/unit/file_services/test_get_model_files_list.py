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
from unittest.mock import patch

import pytest
from fastapi import HTTPException, status

from flip_api.db.models.main_models import UploadedFiles
from flip.file_services.get_model_files_list import get_model_files_list

# filepath: /app/src/flip/file_services/test_get_model_files_list.py


@pytest.fixture
def mock_db_files():
    """Create sample files for testing."""
    model_id = uuid.uuid4()
    file1 = UploadedFiles(
        id=uuid.uuid4(),
        name="test_file1.txt",
        size=1024,
        type="text/plain",
        status="PROCESSED",
        model_id=model_id,
        created=datetime.utcnow(),
        modified=datetime.utcnow(),
    )
    file2 = UploadedFiles(
        id=uuid.uuid4(),
        name="test_file2.csv",
        size=2048,
        type="text/csv",
        status="PROCESSED",
        model_id=model_id,
        created=datetime.utcnow(),
        modified=datetime.utcnow(),
    )
    return [file1, file2], model_id


@pytest.fixture
def mock_session(mock_db_files):
    """Mock database session."""
    files, _ = mock_db_files

    class MockSession:
        def exec(self, query):
            class MockResult:
                def all(self):
                    return files

            return MockResult()

    return MockSession()


def test_get_model_files_list_success(mock_session, mock_db_files):
    """Test successfully retrieving model files."""
    files, model_id = mock_db_files

    with patch("flip.file_services.get_model_files_list.can_access_model", return_value=True):
        result = get_model_files_list(model_id=model_id, db=mock_session, user_id="test-user-id")

        assert len(result) == 2
        assert result[0]["name"] == "test_file1.txt"
        assert result[0]["size"] == 1024
        assert result[0]["type"] == "text/plain"
        assert result[0]["status"] == "PROCESSED"
        assert result[0]["modelId"] == str(model_id)
        assert "created" in result[0]
        assert "modified" in result[0]


def test_get_model_files_list_access_denied(mock_session, mock_db_files):
    """Test access denied when user doesn't have permission."""
    _, model_id = mock_db_files

    with patch("flip.file_services.get_model_files_list.can_access_model", return_value=False):
        with pytest.raises(HTTPException) as exc_info:
            get_model_files_list(model_id=model_id, db=mock_session, user_id="unauthorized-user")

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
        assert "denied access" in exc_info.value.detail


def test_get_model_files_list_empty_result():
    """Test when model has no files."""
    model_id = uuid.uuid4()

    class EmptyMockSession:
        def exec(self, query):
            class MockResult:
                def all(self):
                    return []

            return MockResult()

    with patch("flip.file_services.get_model_files_list.can_access_model", return_value=True):
        result = get_model_files_list(model_id=model_id, db=EmptyMockSession(), user_id="test-user-id")

        assert isinstance(result, list)
        assert len(result) == 0


def test_get_model_files_list_unexpected_error(mock_session, mock_db_files):
    """Test handling of unexpected errors."""
    _, model_id = mock_db_files

    with patch("flip.file_services.get_model_files_list.can_access_model", return_value=True):
        with patch.object(mock_session, "exec", side_effect=Exception("Unexpected database error")):
            with pytest.raises(HTTPException) as exc_info:
                get_model_files_list(model_id=model_id, db=mock_session, user_id="test-user-id")

            assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
            assert "Internal server error" in exc_info.value.detail
