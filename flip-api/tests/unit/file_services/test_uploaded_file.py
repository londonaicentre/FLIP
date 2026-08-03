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
from flip_api.db.models.main_models import UploadedFiles
from flip_api.domain.schemas.file import FileUploadStatus
from flip_api.main import app

client = TestClient(app)

_USER_ID = uuid.uuid4()
_MODEL_ID = uuid.uuid4()
_FILE_NAME = "weights.pt"


@pytest.fixture
def override_auth_dependencies():
    """Inject a deterministic user_id and a mock DB session into the endpoint."""
    mock_session = MagicMock()
    app.dependency_overrides[get_session] = lambda: mock_session
    app.dependency_overrides[verify_token] = lambda: _USER_ID
    yield mock_session
    app.dependency_overrides.clear()


@pytest.fixture
def mocked_settings():
    settings = Settings(UPLOADED_MODEL_FILES_BUCKET="s3://test-uploaded-bucket/uploaded")
    with patch("flip_api.file_services.uploaded_file.get_settings", return_value=settings):
        yield settings


@pytest.fixture
def mock_s3():
    """S3 stub: the staged object exists with fixed metadata."""
    with patch("flip_api.file_services.uploaded_file.S3Client") as mock_client:
        instance = MagicMock()
        instance.object_exists.return_value = True
        instance.head_object.return_value = {"ContentLength": 1024, "ContentType": "application/octet-stream"}
        mock_client.return_value = instance
        yield instance


@pytest.fixture
def mock_reconcile():
    """Stub the background reconcile so tests never open a real DB session.

    Patched in the endpoint module's namespace — ``BackgroundTasks`` holds
    whatever object the route passed to ``add_task``, and the TestClient runs
    it synchronously after the response is built.
    """
    with patch("flip_api.file_services.uploaded_file.reconcile_uploaded_file_in_background") as mock_fn:
        yield mock_fn


def test_returns_403_when_user_cannot_modify_model(
    override_auth_dependencies, mocked_settings, mock_s3, mock_reconcile
):
    mock_session = override_auth_dependencies
    with patch("flip_api.file_services.uploaded_file.can_modify_model", return_value=False):
        response = client.post(f"/api/files/process-scanned-file/{_MODEL_ID}/{_FILE_NAME}")

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert str(_USER_ID) in response.json()["detail"]
    mock_session.add.assert_not_called()
    mock_session.commit.assert_not_called()
    mock_s3.head_object.assert_not_called()
    mock_reconcile.assert_not_called()


def test_returns_404_when_staged_object_absent(
    override_auth_dependencies, mocked_settings, mock_s3, mock_reconcile
):
    """A registration for a file that never landed in S3 must not create a row."""
    mock_session = override_auth_dependencies
    mock_s3.object_exists.return_value = False

    with patch("flip_api.file_services.uploaded_file.can_modify_model", return_value=True):
        response = client.post(f"/api/files/process-scanned-file/{_MODEL_ID}/{_FILE_NAME}")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    mock_session.add.assert_not_called()
    mock_session.commit.assert_not_called()
    mock_reconcile.assert_not_called()


def test_registers_new_file_as_scanning_and_schedules_reconcile(
    override_auth_dependencies, mocked_settings, mock_s3, mock_reconcile
):
    mock_session = override_auth_dependencies
    mock_session.exec.return_value.first.return_value = None  # no existing row -> insert path

    with patch("flip_api.file_services.uploaded_file.can_modify_model", return_value=True):
        response = client.post(f"/api/files/process-scanned-file/{_MODEL_ID}/{_FILE_NAME}")

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"message": "File registered for scanning", "status": "SCANNING"}

    mock_session.add.assert_called_once()
    inserted = mock_session.add.call_args.args[0]
    # Regression guard: the legacy insert path passed ``status=<str>`` while
    # the update path passed the enum — both must be the enum now.
    assert inserted.status is FileUploadStatus.SCANNING
    assert inserted.size == 1024
    assert inserted.type == "application/octet-stream"
    assert inserted.updated_at is not None
    mock_session.commit.assert_called_once()

    mock_reconcile.assert_called_once_with(_MODEL_ID, _FILE_NAME)


def test_reregistration_resets_terminal_status_to_scanning(
    override_auth_dependencies, mocked_settings, mock_s3, mock_reconcile
):
    """Re-uploading a file previously judged INFECTED is the only way out of
    that status — the upsert must reset it to SCANNING for the fresh object."""
    mock_session = override_auth_dependencies
    existing = UploadedFiles(
        name=_FILE_NAME,
        status=FileUploadStatus.INFECTED,
        size=1,
        type="deleted",
        model_id=_MODEL_ID,
    )
    mock_session.exec.return_value.first.return_value = existing

    with patch("flip_api.file_services.uploaded_file.can_modify_model", return_value=True):
        response = client.post(f"/api/files/process-scanned-file/{_MODEL_ID}/{_FILE_NAME}")

    assert response.status_code == status.HTTP_200_OK
    assert existing.status is FileUploadStatus.SCANNING
    assert existing.size == 1024
    assert existing.updated_at is not None
    mock_session.add.assert_not_called()
    mock_session.commit.assert_called_once()
    mock_reconcile.assert_called_once_with(_MODEL_ID, _FILE_NAME)


def test_returns_422_for_non_uuid_model_id(override_auth_dependencies, mocked_settings, mock_s3, mock_reconcile):
    """FastAPI must reject malformed model_id at the path-parameter layer."""
    with patch("flip_api.file_services.uploaded_file.can_modify_model", return_value=True) as can_modify:
        response = client.post(f"/api/files/process-scanned-file/not-a-uuid/{_FILE_NAME}")

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    can_modify.assert_not_called()


@pytest.mark.parametrize(
    "bad_file_name",
    [
        # ``..`` (literal) is collapsed by HTTP-client URL normalisation
        # before the request is sent, so it never reaches the validator;
        # the percent-encoded forms below are the real attack shape.
        "..%2Fescape.pt",
        "subdir%2Ffile.pt",
        "subdir%5Cfile.pt",
        "file%00.pt",
    ],
)
def test_rejects_path_traversal_file_name(
    override_auth_dependencies, mocked_settings, mock_s3, mock_reconcile, bad_file_name
):
    """The ``SafeFileName`` path-param validator must short-circuit before any
    S3 access — the file name is later used as an S3 key segment and as a
    local file name for the picklescan download."""
    with patch("flip_api.file_services.uploaded_file.can_modify_model", return_value=True):
        response = client.post(f"/api/files/process-scanned-file/{_MODEL_ID}/{bad_file_name}")

    # Accept either 422 (validator rejects) or 404 (Starlette routing rejects
    # the raw segment); the contract is that no S3 call happens.
    assert response.status_code in (status.HTTP_404_NOT_FOUND, status.HTTP_422_UNPROCESSABLE_ENTITY)
    mock_s3.object_exists.assert_not_called()
    mock_reconcile.assert_not_called()


def test_unhandled_error_does_not_echo_exception_text(
    override_auth_dependencies, mocked_settings, mock_s3, mock_reconcile
):
    """The 500 detail must stay generic — the legacy handler interpolated
    ``str(e)`` into the response body."""
    mock_session = override_auth_dependencies
    mock_session.exec.side_effect = Exception("SECRET-INTERNAL-DETAIL")

    with patch("flip_api.file_services.uploaded_file.can_modify_model", return_value=True):
        response = client.post(f"/api/files/process-scanned-file/{_MODEL_ID}/{_FILE_NAME}")

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert response.json()["detail"] == "Internal server error"
    assert "SECRET-INTERNAL-DETAIL" not in response.text
    mock_reconcile.assert_not_called()
