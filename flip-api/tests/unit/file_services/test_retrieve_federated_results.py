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
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError
from fastapi import HTTPException, status
from fastapi.testclient import TestClient

from flip_api.auth.dependencies import verify_token
from flip.config import Settings
from flip.db.database import get_session
from flip.db.models.main_models import Model
from flip.file_services.retrieve_federated_results import retrieve_federated_results
from flip.main import app
from flip.utils.s3_client import S3Client
from tests.fixtures.db_fixtures import ModelFactory, ProjectFactory


@pytest.fixture
def mock_logger():
    """Mock logger for testing."""
    with patch("flip.file_services.retrieve_federated_results.logger") as mock_logger:
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
def empty_db_session():
    """Mock database session that returns empty results."""

    class MockSession:
        def exec(self, query):
            class MockResult:
                def first(self):
                    return None

            return MockResult()

    return MockSession()


@pytest.fixture
def s3_mock_success():
    """Mock S3 client for success case."""
    with patch("flip.file_services.retrieve_federated_results.S3Client") as mock_s3:
        mock_instance = MagicMock()
        mock_s3.return_value = mock_instance

        mock_instance.list_objects.return_value = [
            "model-id/file1.csv",
            "model-id/file2.csv",
        ]

        mock_instance.get_presigned_url.side_effect = [
            "https://test-bucket.s3.amazonaws.com/model-id/file1.csv",
            "https://test-bucket.s3.amazonaws.com/model-id/file2.csv",
        ]

        yield mock_instance


@pytest.fixture
def s3_mock_empty():
    """Mock S3 client returning empty results."""
    with patch("flip.file_services.retrieve_federated_results.S3Client") as mock_s3:
        mock_instance = MagicMock()
        mock_s3.return_value = mock_instance
        mock_instance.list_objects.return_value = []
        yield mock_instance


@pytest.fixture
def s3_mock_error():
    """Mock S3 client that raises an error."""
    with patch("flip.file_services.retrieve_federated_results.S3Client") as mock_s3:
        mock_instance = MagicMock()
        mock_s3.return_value = mock_instance
        mock_instance.list_objects.side_effect = Exception("S3 error")
        yield mock_instance


@pytest.fixture
def s3_mock_presigned_error():
    """Mock S3 client with presigned URL error."""
    with patch("flip.file_services.retrieve_federated_results.S3Client") as mock_s3:
        mock_instance = MagicMock()
        mock_s3.return_value = mock_instance
        mock_instance.list_objects.return_value = ["model-id/file1.csv"]
        mock_instance.get_presigned_url.side_effect = Exception("Presigned URL error")
        yield mock_instance


@pytest.fixture
def mocked_settings():
    mock = Settings(
        AWS_REGION="mock-region",
        AWS_COGNITO_USER_POOL_ID="eu-west-2_123456789",
        AWS_SES_ADMIN_EMAIL_ADDRESS="admin@example.com",
        AWS_SES_SENDER_EMAIL_ADDRESS="sender@example.com",
        UPLOADED_MODEL_FILES_BUCKET="mock-bucket",
        UPLOADED_FEDERATED_DATA_BUCKET="s3://mock-bucket-uploaded/uploaded_federated_data",
        PRIVATE_API_KEY="mock-api-key",
        FL_APP_BASE_BUCKET="s3://mock-bucket-base-app/base_files",
        SCANNED_MODEL_FILES_BUCKET="s3://mock-bucket-scanned/model_files",
        FL_APP_DESTINATION_BUCKET="s3://mock-bucket-dest/dest_files",
    )
    with patch("flip.file_services.retrieve_federated_results.get_settings", return_value=mock):
        yield mock


@pytest.fixture(autouse=True)
def override_dependencies():
    """Override auth dependency for testing."""
    dummy_user_id = uuid4()
    # Store session mock globally so tests can access/modify it
    mock_session = MagicMock()
    app.dependency_overrides[get_session] = lambda: mock_session
    app.dependency_overrides[verify_token] = lambda: dummy_user_id
    yield mock_session
    app.dependency_overrides = {}


client = TestClient(app)


def test_retrieve_federated_results_endpoint_calls_function(override_dependencies, mocked_settings):
    """Ensure the endpoint exists and calls retrieve_federated_results()."""
    model_id = uuid4()  # This does not exist
    expected_output = ["https://test-bucket.s3.amazonaws.com/model-id/file1.csv"]

    # Modify the session mock to simulate a found model
    mock_model = MagicMock(spec=Model)
    override_dependencies.exec.return_value.first.return_value = mock_model

    with (
        patch("flip.file_services.retrieve_federated_results.can_access_model", return_value=True),
        patch("flip.file_services.retrieve_federated_results.S3Client") as mock_s3_client,
    ):
        # Mock S3 list_objects and get_presigned_url
        mock_s3 = mock_s3_client.return_value
        mock_s3.list_objects.return_value = ["model-id/file1.csv"]
        mock_s3.get_presigned_url.return_value = expected_output[0]

        client = TestClient(app)
        response = client.get(f"/files/model/{model_id}/fl/results")

        assert response.status_code == 200
        assert response.json() == expected_output


class TestRetrieveFederatedResults:
    """Tests for retrieve_federated_results function."""

    def test_retrieve_success(self, mock_db_session, s3_mock_success, mocked_settings, sample_model_id, user_id):
        """Test successful retrieval of federated results."""
        with patch("flip.file_services.retrieve_federated_results.can_access_model", return_value=True):
            result = retrieve_federated_results(model_id=sample_model_id, db=mock_db_session, user_id=user_id)
            assert len(result) == 2
            assert result[0] == "https://test-bucket.s3.amazonaws.com/model-id/file1.csv"
            assert result[1] == "https://test-bucket.s3.amazonaws.com/model-id/file2.csv"

    def test_access_denied(self, mock_db_session, mocked_settings, sample_model_id, user_id):
        """Test handling of unauthorized access to model."""
        with patch("flip.file_services.retrieve_federated_results.can_access_model", return_value=False):
            with pytest.raises(HTTPException) as exc_info:
                retrieve_federated_results(model_id=sample_model_id, db=mock_db_session, user_id=user_id)

            assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
            assert "denied access to this model" in exc_info.value.detail

    def test_model_not_found(self, empty_db_session, mocked_settings, sample_model_id, user_id):
        """Test handling of non-existent model."""
        with patch("flip.file_services.retrieve_federated_results.can_access_model", return_value=True):
            with pytest.raises(HTTPException) as exc_info:
                retrieve_federated_results(model_id=sample_model_id, db=empty_db_session, user_id=user_id)

            assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
            assert "does not exist" in exc_info.value.detail

    def test_s3_listing_error(self, mock_db_session, s3_mock_error, mocked_settings, sample_model_id, user_id):
        """Test handling of S3 listing error."""
        with patch("flip.file_services.retrieve_federated_results.can_access_model", return_value=True):
            with pytest.raises(HTTPException) as exc_info:
                retrieve_federated_results(model_id=sample_model_id, db=mock_db_session, user_id=user_id)

            assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
            assert "An error occurred when finding the result data" in exc_info.value.detail

    def test_no_files_found(self, mock_db_session, s3_mock_empty, mocked_settings, sample_model_id, user_id):
        """Test handling of no files found in S3."""
        with patch("flip.file_services.retrieve_federated_results.can_access_model", return_value=True):
            with pytest.raises(HTTPException) as exc_info:
                retrieve_federated_results(model_id=sample_model_id, db=mock_db_session, user_id=user_id)

            assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
            assert "No result data was found" in exc_info.value.detail

    def test_presigned_url_error(
        self, mock_db_session, s3_mock_presigned_error, mocked_settings, sample_model_id, user_id
    ):
        """Test handling of error when generating presigned URLs."""
        with patch("flip.file_services.retrieve_federated_results.can_access_model", return_value=True):
            with pytest.raises(HTTPException) as exc_info:
                retrieve_federated_results(model_id=sample_model_id, db=mock_db_session, user_id=user_id)

            assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
            assert "An error occurred when attempting to retrieve the files" in exc_info.value.detail

    def test_unexpected_error(self, mock_db_session, mocked_settings, sample_model_id, user_id):
        """Test handling of unexpected general errors."""
        with patch(
            "flip.file_services.retrieve_federated_results.can_access_model",
            side_effect=Exception("Unexpected error"),
        ):
            with pytest.raises(HTTPException) as exc_info:
                retrieve_federated_results(model_id=sample_model_id, db=mock_db_session, user_id=user_id)

            assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
            assert "Internal server error" in exc_info.value.detail


class TestS3Client:
    """Tests for S3Client class."""

    def test_init(self):
        """Test initialization of S3Client."""
        with patch("boto3.client") as mock_boto:
            S3Client()
            mock_boto.assert_called_once()

    def test_list_objects_success(self):
        """Test successful listing of objects in S3."""
        with patch("boto3.client") as mock_boto:
            mock_client = MagicMock()
            mock_boto.return_value = mock_client
            mock_client.list_objects_v2.return_value = {
                "Contents": [{"Key": "model-id/file1.csv"}, {"Key": "model-id/file2.csv"}]
            }
            expected_result = [
                "s3://test-bucket/model-id/file1.csv",
                "s3://test-bucket/model-id/file2.csv",
            ]

            s3_client = S3Client()
            result = s3_client.list_objects("s3://test-bucket/test-prefix")

            assert result == expected_result
            mock_client.list_objects_v2.assert_called_once_with(Bucket="test-bucket", Prefix="test-prefix")

    def test_list_objects_error(self):
        """Test error handling when listing objects in S3."""
        test_bucket = "s3://test-bucket"

        with patch("boto3.client") as mock_boto:
            mock_client = MagicMock()
            mock_boto.return_value = mock_client

            error_response = {"Error": {"Code": "NoSuchBucket", "Message": "The bucket does not exist"}}
            mock_client.list_objects_v2.side_effect = ClientError(error_response, "ListObjectsV2")

            s3_client = S3Client()
            with pytest.raises(Exception, match="Error listing objects") as exc_info:
                s3_client.list_objects(test_bucket)

            assert f"Error listing objects under '{test_bucket}'" in str(exc_info.value)

    def test_get_presigned_url_success(self):
        """Test successful generation of presigned URL."""
        with patch("boto3.client") as mock_boto:
            mock_client = MagicMock()
            mock_boto.return_value = mock_client
            mock_client.generate_presigned_url.return_value = "https://test-url"

            s3_client = S3Client()
            url = s3_client.get_presigned_url("s3://test-bucket/test-key")

            assert url == "https://test-url"
            mock_client.generate_presigned_url.assert_called_once()

    def test_get_presigned_url_error(self):
        """Test error handling when generating presigned URL."""
        with patch("boto3.client") as mock_boto:
            mock_client = MagicMock()
            mock_boto.return_value = mock_client

            error_response = {"Error": {"Code": "AccessDenied", "Message": "Access denied"}}
            mock_client.generate_presigned_url.side_effect = ClientError(error_response, "GetObject")

            s3_client = S3Client()
            with pytest.raises(ClientError) as exc_info:
                s3_client.get_presigned_url("test-bucket", "test-key")

            assert "An error occurred (AccessDenied)" in str(exc_info.value)
