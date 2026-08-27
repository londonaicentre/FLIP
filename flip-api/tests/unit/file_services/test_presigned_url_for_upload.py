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

import logging
import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from flip_api.auth.dependencies import verify_token
from flip_api.config import Settings
from flip_api.db.database import get_session
from flip_api.main import app
from tests.unit._log_policy import _FAKE_SIGNED_URL, _assert_logs_have_no_presigned_url

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
    # Use an ``s3://`` prefix so ``parse_s3_path`` exercises the same code
    # path as production — without the scheme, ``urlparse`` sets ``netloc=""``
    # and the production parser would silently emit ``bucket=`` empty.
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
                "key": f"uploads/{_MODEL_ID}/weights.pt",
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
            json={"fileName": "weights.pt"},
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
            json={"fileName": "weights.pt"},
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
                "fileName": "weights.pt",
                "contentType": "application/octet-stream",
            },
        )

    assert response.status_code == status.HTTP_200_OK, response.text
    body = response.json()
    assert body["url"].startswith("https://")
    assert body["fields"]["Content-Type"] == "application/octet-stream"
    assert body["maxBytes"] == mocked_settings.MAX_MODEL_FILE_BYTES

    mock_s3_client.get_put_presigned_post.assert_called_once_with(
        f"{mocked_settings.UPLOADED_MODEL_FILES_BUCKET}/{_MODEL_ID}/weights.pt",
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
            json={"fileName": "weights.pt"},
        )

    assert response.status_code == status.HTTP_200_OK, response.text
    mock_s3_client.get_put_presigned_post.assert_called_once_with(
        f"{mocked_settings.UPLOADED_MODEL_FILES_BUCKET}/{_MODEL_ID}/weights.pt",
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
            json={"fileName": "weights.pt"},
        )

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR


def test_endpoint_returns_422_for_non_uuid_model_id(
    override_auth_dependencies, mocked_settings, mock_s3_client
):
    response = client.post(
        "/api/files/preSignedUrl/model/not-a-uuid",
        json={"fileName": "weights.pt"},
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    mock_s3_client.get_put_presigned_post.assert_not_called()


def test_endpoint_rejects_path_traversal_filename(
    override_auth_dependencies, mocked_settings, mock_s3_client
):
    """The ``fileName`` validator must short-circuit before any S3 call."""
    response = client.post(
        f"/api/files/preSignedUrl/model/{_MODEL_ID}",
        json={"fileName": "../escape.pt"},
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY, response.text
    mock_s3_client.get_put_presigned_post.assert_not_called()


def test_endpoint_success_path_does_not_log_signed_url(
    caplog, override_auth_dependencies, mocked_settings, mock_s3_client
):
    """The success path must never log the policy's signed URL or fields."""
    caplog.set_level(logging.DEBUG, logger="uvicorn")
    mock_session = override_auth_dependencies
    _existing_model(mock_session)

    # Have the mocked policy return a realistic SigV4-style URL so the
    # log-policy assertion exercises the same surface as production.
    mock_s3_client.get_put_presigned_post.return_value = {
        "url": _FAKE_SIGNED_URL,
        "fields": {"Content-Type": "application/octet-stream"},
    }

    with patch(
        "flip_api.file_services.presigned_url_for_upload.can_modify_model",
        return_value=True,
    ):
        response = client.post(
            f"/api/files/preSignedUrl/model/{_MODEL_ID}",
            json={"fileName": "weights.pt"},
        )

    assert response.status_code == status.HTTP_200_OK, response.text
    _assert_logs_have_no_presigned_url(caplog.records)


def test_endpoint_redacts_url_when_s3_raises(
    caplog, override_auth_dependencies, mocked_settings, mock_s3_client
):
    """If ``S3Client.get_put_presigned_post`` raises with a URL embedded in the
    exception message, the route's error handler must not leak it to the log.

    Exception paths evolve more often than happy paths, so pin redaction here
    even though boto's own ``ClientError`` does not carry the URL today.
    """
    caplog.set_level(logging.DEBUG, logger="uvicorn")
    mock_session = override_auth_dependencies
    _existing_model(mock_session)
    mock_s3_client.get_put_presigned_post.side_effect = Exception(_FAKE_SIGNED_URL)

    with patch(
        "flip_api.file_services.presigned_url_for_upload.can_modify_model",
        return_value=True,
    ):
        response = client.post(
            f"/api/files/preSignedUrl/model/{_MODEL_ID}",
            json={"fileName": "weights.pt"},
        )

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR, response.text
    assert _FAKE_SIGNED_URL not in response.text
    _assert_logs_have_no_presigned_url(caplog.records)


def test_endpoint_redacts_url_when_unhandled_error(
    caplog, override_auth_dependencies, mocked_settings, mock_s3_client
):
    """The outer ``except Exception`` must not leak a URL via ``logger.error``.

    Force the access check to raise with a URL in the message — without the
    redaction in place this would land in the unhandled-error log line.
    """
    caplog.set_level(logging.DEBUG, logger="uvicorn")

    with patch(
        "flip_api.file_services.presigned_url_for_upload.can_modify_model",
        side_effect=Exception(_FAKE_SIGNED_URL),
    ):
        response = client.post(
            f"/api/files/preSignedUrl/model/{_MODEL_ID}",
            json={"fileName": "weights.pt"},
        )

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR, response.text
    assert _FAKE_SIGNED_URL not in response.text
    _assert_logs_have_no_presigned_url(caplog.records)


@pytest.mark.parametrize(
    "file_name",
    [
        "trainer.py",
        "config.json",
        # Flower's per-app run config — every Flower template ships one, so
        # rejecting .toml would block every Flower upload.
        "config.toml",
        "weights.pt",
        "WEIGHTS.PTH",
        "aux.pkl",
        "notes.txt",
        "conf.yaml",
        "w.safetensors",
    ],
)
def test_endpoint_accepts_whitelisted_extensions(
    override_auth_dependencies, mocked_settings, mock_s3_client, file_name
):
    """Every default-whitelisted extension must mint a policy, case-insensitively."""
    mock_session = override_auth_dependencies
    _existing_model(mock_session)

    with patch(
        "flip_api.file_services.presigned_url_for_upload.can_modify_model",
        return_value=True,
    ):
        response = client.post(
            f"/api/files/preSignedUrl/model/{_MODEL_ID}",
            json={"fileName": file_name},
        )

    assert response.status_code == status.HTTP_200_OK, response.text


@pytest.mark.parametrize(
    ("file_name", "reported_suffix"),
    [
        ("payload.exe", ".exe"),
        ("archive.zip", ".zip"),
        ("bundle.tar.gz", ".gz"),
        ("dropper.sh", ".sh"),
        ("no_extension", "<none>"),
    ],
)
def test_endpoint_rejects_non_whitelisted_extensions(
    override_auth_dependencies, mocked_settings, mock_s3_client, file_name, reported_suffix
):
    """Disallowed file types must be refused before any policy is minted, with
    a detail message naming the offending suffix and the allowed set."""
    mock_session = override_auth_dependencies
    _existing_model(mock_session)

    with patch(
        "flip_api.file_services.presigned_url_for_upload.can_modify_model",
        return_value=True,
    ):
        response = client.post(
            f"/api/files/preSignedUrl/model/{_MODEL_ID}",
            json={"fileName": file_name},
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.text
    detail = response.json()["detail"]
    assert f"File type '{reported_suffix}' is not allowed" in detail
    assert ".py" in detail  # the allowed set is spelled out for the UI to surface
    mock_s3_client.get_put_presigned_post.assert_not_called()
