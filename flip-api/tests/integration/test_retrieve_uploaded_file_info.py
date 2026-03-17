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

from flip_api.db.models.main_models import UploadedFiles


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


@pytest.mark.skip
class TestIntegration:
    """Integration tests for the file info endpoints."""

    def test_get_files_info_integration(self, client, sample_files):
        """Integration test for the GET endpoint."""
        _, sample_file_ids, _ = sample_files
        file_ids_str = ",".join(str(fid) for fid in sample_file_ids[:2])

        with patch("flip_api.auth.dependencies.verify_token", return_value="test-user-id"):
            with patch("sqlmodel.Session.exec") as mock_exec:
                mock_result = MagicMock()
                mock_result.all.return_value = sample_files[0]
                mock_exec.return_value = mock_result

                response = client.get(f"/files/info?file_ids={file_ids_str}")

                assert response.status_code == 200
                data = response.json()
                assert len(data) == 2
                assert data[0]["name"] == "test_file1.txt"

    def test_post_files_info_integration(self, client, sample_files):
        """Integration test for the POST endpoint."""
        _, sample_file_ids, _ = sample_files

        with patch("flip_api.auth.dependencies.verify_token", return_value="test-user-id"):
            with patch("sqlmodel.Session.exec") as mock_exec:
                mock_result = MagicMock()
                mock_result.all.return_value = sample_files[0]
                mock_exec.return_value = mock_result

                response = client.post("/api/files/info", json={"ids": [str(id) for id in sample_file_ids[:2]]})

                assert response.status_code == 200
                data = response.json()
                assert len(data) == 2
                assert data[0]["name"] == "test_file1.txt"
