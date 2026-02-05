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
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException, status

from flip_api.config import Settings
from flip_api.file_services.retrieve_model_files_list import (
    ModelFiles,
    ModelFilesList,
    retrieve_model_files_list,
)
from tests.fixtures.db_fixtures import ModelFactory, ProjectFactory


@pytest.fixture
def mock_logger():
    """Mock logger for testing."""
    with patch("flip_api.file_services.retrieve_model_files_list.logger") as mock_logger:
        yield mock_logger


@pytest.fixture
def sample_model_id():
    """Create sample model ID for testing."""
    return uuid.uuid4()


@pytest.fixture
def sample_model(sample_model_id):
    """Create sample model for testing using factory."""
    project = ProjectFactory(deleted=False)
    model = ModelFactory(id=sample_model_id, project_id=project.id)
    return model, project


@pytest.fixture
def s3_mock_success():
    """Mock S3 client for success case with various file types."""
    with patch("flip_api.file_services.retrieve_model_files_list.S3Client") as mock_s3:
        mock_instance = MagicMock()
        mock_s3.return_value = mock_instance

        # Mock response with different file types
        mock_instance.list_objects.return_value = {
            "Contents": [
                {"Key": "model-id/algo/monaialgo.py"},
                {"Key": "model-id/opener/monaiopener.py"},
                {"Key": "model-id/model/monai-test.pth.tar"},
                {"Key": "model-id/other/unrelated.txt"},
            ]
        }

        yield mock_instance


@pytest.fixture
def s3_mock_empty():
    """Mock S3 client returning empty results."""
    with patch("flip_api.file_services.retrieve_model_files_list.S3Client") as mock_s3:
        mock_instance = MagicMock()
        mock_s3.return_value = mock_instance
        mock_instance.list_objects.return_value = {}
        yield mock_instance


@pytest.fixture
def s3_mock_error():
    """Mock S3 client that raises an error."""
    with patch("flip_api.file_services.retrieve_model_files_list.S3Client") as mock_s3:
        mock_instance = MagicMock()
        mock_s3.return_value = mock_instance
        mock_instance.list_objects.side_effect = Exception("S3 error")
        yield mock_instance


@pytest.fixture
def mocked_settings():
    mock = Settings(
        SCANNED_MODEL_FILES_BUCKET="test-bucket",
    )
    with patch("flip_api.file_services.retrieve_model_files_list.get_settings", return_value=mock):
        yield mock


class TestModelFilesClasses:
    """Tests for ModelFiles and ModelFilesList classes."""

    def test_model_files_defaults(self):
        """Test ModelFiles initialization with default values."""
        model_files = ModelFiles()
        assert model_files.algo is None
        assert model_files.opener is None
        assert model_files.model is None

    def test_model_files_custom_values(self):
        """Test ModelFiles initialization with custom values."""
        model_files = ModelFiles(algo="path/to/algo.py", opener="path/to/opener.py", model="path/to/model.pth")
        assert model_files.algo == "path/to/algo.py"
        assert model_files.opener == "path/to/opener.py"
        assert model_files.model == "path/to/model.pth"

    def test_model_files_list(self):
        """Test ModelFilesList initialization."""
        model_files = ModelFiles(algo="path/to/algo.py", opener="path/to/opener.py", model="path/to/model.pth")
        model_files_list = ModelFilesList(files=model_files)
        assert model_files_list.files == model_files
        assert model_files_list.files.algo == "path/to/algo.py"
        assert model_files_list.files.opener == "path/to/opener.py"
        assert model_files_list.files.model == "path/to/model.pth"

    def test_model_files_dict_conversion(self):
        """Test dict conversion for ModelFiles."""
        model_files = ModelFiles(algo="path/to/algo.py", opener="path/to/opener.py", model="path/to/model.pth")
        model_dict = model_files.dict()
        assert model_dict == {"algo": "path/to/algo.py", "opener": "path/to/opener.py", "model": "path/to/model.pth"}

    def test_model_files_list_dict_conversion(self):
        """Test dict conversion for ModelFilesList."""
        model_files = ModelFiles(algo="path/to/algo.py", opener="path/to/opener.py", model="path/to/model.pth")
        model_files_list = ModelFilesList(files=model_files)
        list_dict = model_files_list.dict()
        assert list_dict == {
            "files": {"algo": "path/to/algo.py", "opener": "path/to/opener.py", "model": "path/to/model.pth"}
        }


class TestRetrieveModelFilesList:
    """Tests for retrieve_model_files_list function."""

    def test_access_denied(self, mock_db_session, sample_model_id):
        """Test handling of unauthorized access to model."""
        with patch("flip_api.file_services.retrieve_model_files_list.can_access_model", return_value=False):
            with pytest.raises(HTTPException) as exc_info:
                retrieve_model_files_list(model_id=sample_model_id, db=mock_db_session, user_id="test-user-id")

            assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
            assert "denied access to this model" in exc_info.value.detail

    def test_s3_listing_error(self, mock_db_session, s3_mock_error, sample_model_id):
        """Test handling of S3 listing error."""
        with patch("flip_api.file_services.retrieve_model_files_list.can_access_model", return_value=True):
            with pytest.raises(HTTPException) as exc_info:
                retrieve_model_files_list(model_id=sample_model_id, db=mock_db_session, user_id="test-user-id")

            assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
            assert "An error occurred when finding the result data" in exc_info.value.detail

    def test_empty_results(self, mock_db_session, s3_mock_empty, sample_model_id):
        """Test handling of empty results from S3."""
        with pytest.raises(HTTPException) as exc_info:
            retrieve_model_files_list(model_id=sample_model_id, db=mock_db_session, user_id="test-user-id")
        exc_info.value.status_code == status.HTTP_404_NOT_FOUND

    def test_unexpected_error(self, mock_db_session, sample_model_id):
        """Test handling of unexpected general errors."""
        with patch(
            "flip_api.file_services.retrieve_model_files_list.can_access_model",
            side_effect=Exception("Unexpected error"),
        ):
            with pytest.raises(HTTPException) as exc_info:
                retrieve_model_files_list(model_id=sample_model_id, db=mock_db_session, user_id="test-user-id")

            assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
            assert "Internal server error" in exc_info.value.detail
