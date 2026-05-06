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
from fastapi import status
from fastapi.testclient import TestClient

from flip_api.auth.dependencies import verify_token
from flip_api.config import Settings
from flip_api.db.database import get_session
from flip_api.main import app

# A fixed user / model pair keeps assertion strings stable across runs.
_USER_ID = uuid.uuid4()
_MODEL_ID = uuid.uuid4()

client = TestClient(app)


@pytest.fixture
def override_auth_dependencies():
    """Inject a deterministic ``user_id`` and a mock DB session into the endpoint."""
    mock_session = MagicMock()
    app.dependency_overrides[get_session] = lambda: mock_session
    app.dependency_overrides[verify_token] = lambda: _USER_ID
    yield mock_session
    app.dependency_overrides.clear()


@pytest.fixture
def mocked_settings():
    """Pin the bucket and the size cap so test assertions are exact."""
    settings = Settings(
        UPLOADED_MODEL_FILES_BUCKET="s3://test-uploaded-bucket/uploads",
        MAX_MODEL_FILE_BYTES=1234,
        PRE_SIGNED_URL_EXPIRATION_SECONDS=42,
    )
    with patch(
        "flip_api.file_services.presigned_url_for_upload.get_settings",
        return_value=settings,
    ):
        yield settings


@pytest.fixture
def mock_s3_client():
    """Mock the ``S3Client`` constructor so the endpoint never touches AWS."""
    with patch("flip_api.file_services.presigned_url_for_upload.S3Client") as mock_cls:
        instance = MagicMock()
        instance.get_put_presigned_post.return_value = {
            "url": "https://test-uploaded-bucket.s3.amazonaws.com/",
            "fields": {
                "key": f"uploads/{_MODEL_ID}/weights.bin",
                "Content-Type": "application/octet-stream",
                "policy": "POLICY",
                "x-amz-signature": "SIG",
            },
        }
        mock_cls.return_value = instance
        yield instance


def _existing_model(mock_session: MagicMock) -> None:
    """Make the SQL lookup for the model return a truthy row."""
    mock_session.exec.return_value.first.return_value = MagicMock()


def test_endpoint_returns_403_when_user_cannot_modify_model(
    override_auth_dependencies, mocked_settings, mock_s3_client
):
    with patch(
        "flip_api.file_services.presigned_url_for_upload.can_modify_model",
        return_value=False,
    ):
        response = client.post(
            f"/api/files/preSignedUrl/model/{_MODEL_ID}",
            json={"fileName": "weights.bin"},
        )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert str(_USER_ID) in response.json()["detail"]
    mock_s3_client.get_put_presigned_post.assert_not_called()


def test_endpoint_returns_404_when_model_missing(
    override_auth_dependencies, mocked_settings, mock_s3_client
):
    mock_session = override_auth_dependencies
    mock_session.exec.return_value.first.return_value = None  # model not found

    with patch(
        "flip_api.file_services.presigned_url_for_upload.can_modify_model",
        return_value=True,
    ):
        response = client.post(
            f"/api/files/preSignedUrl/model/{_MODEL_ID}",
            json={"fileName": "weights.bin"},
        )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    mock_s3_client.get_put_presigned_post.assert_not_called()


def test_endpoint_passes_size_cap_and_content_type_into_policy(
    override_auth_dependencies, mocked_settings, mock_s3_client
):
    """The hot-fix contract: cap from settings + Content-Type from body must
    flow into ``S3Client.get_put_presigned_post``. Without this, the policy
    would issue an unconstrained URL just like the original PUT-only flow.
    """
    mock_session = override_auth_dependencies
    _existing_model(mock_session)

    with patch(
        "flip_api.file_services.presigned_url_for_upload.can_modify_model",
        return_value=True,
    ):
        response = client.post(
            f"/api/files/preSignedUrl/model/{_MODEL_ID}",
            json={
                "fileName": "weights.bin",
                "contentType": "application/octet-stream",
            },
        )

    assert response.status_code == status.HTTP_200_OK, response.text
    body = response.json()
    assert body["url"].startswith("https://")
    assert body["fields"]["Content-Type"] == "application/octet-stream"
    assert body["maxBytes"] == mocked_settings.MAX_MODEL_FILE_BYTES

    mock_s3_client.get_put_presigned_post.assert_called_once_with(
        f"{mocked_settings.UPLOADED_MODEL_FILES_BUCKET}/{_MODEL_ID}/weights.bin",
        max_bytes=mocked_settings.MAX_MODEL_FILE_BYTES,
        content_type="application/octet-stream",
        expiration=mocked_settings.PRE_SIGNED_URL_EXPIRATION_SECONDS,
    )


def test_endpoint_omits_content_type_when_client_does_not_supply_one(
    override_auth_dependencies, mocked_settings, mock_s3_client
):
    """If the client does not declare a Content-Type, the policy still binds
    the size cap but leaves Content-Type unrestricted — so we must forward
    ``None`` rather than fabricating a default that locks in the wrong type.
    """
    mock_session = override_auth_dependencies
    _existing_model(mock_session)

    with patch(
        "flip_api.file_services.presigned_url_for_upload.can_modify_model",
        return_value=True,
    ):
        response = client.post(
            f"/api/files/preSignedUrl/model/{_MODEL_ID}",
            json={"fileName": "weights.bin"},
        )

    assert response.status_code == status.HTTP_200_OK, response.text
    mock_s3_client.get_put_presigned_post.assert_called_once_with(
        f"{mocked_settings.UPLOADED_MODEL_FILES_BUCKET}/{_MODEL_ID}/weights.bin",
        max_bytes=mocked_settings.MAX_MODEL_FILE_BYTES,
        content_type=None,
        expiration=mocked_settings.PRE_SIGNED_URL_EXPIRATION_SECONDS,
    )


def test_endpoint_returns_500_when_s3_client_raises(
    override_auth_dependencies, mocked_settings, mock_s3_client
):
    mock_session = override_auth_dependencies
    _existing_model(mock_session)
    mock_s3_client.get_put_presigned_post.side_effect = Exception("boom")

    with patch(
        "flip_api.file_services.presigned_url_for_upload.can_modify_model",
        return_value=True,
    ):
        response = client.post(
            f"/api/files/preSignedUrl/model/{_MODEL_ID}",
            json={"fileName": "weights.bin"},
        )

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR


def test_endpoint_returns_422_for_non_uuid_model_id(
    override_auth_dependencies, mocked_settings, mock_s3_client
):
    response = client.post(
        "/api/files/preSignedUrl/model/not-a-uuid",
        json={"fileName": "weights.bin"},
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    mock_s3_client.get_put_presigned_post.assert_not_called()
