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

"""The results-upload routes: a job's own credential, a plain zip name, an object under the model's prefix."""

import logging
from collections.abc import Generator
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

from flip_api.auth.access_manager import authenticate_internal_service
from flip_api.auth.job_callback import JOB_CREDENTIAL_HEADER, hash_job_credential
from flip_api.db.database import get_session
from flip_api.domain.schemas.private import RESULTS_UPLOAD_MAX_PARTS, RESULTS_UPLOAD_PART_BYTES
from flip_api.main import app
from flip_api.private_services.results_upload import PART_URL_TTL_SECONDS
from flip_api.utils.s3_client import PresignAudience
from tests.unit._log_policy import _FAKE_SIGNED_URL, _assert_logs_have_no_presigned_url

MODEL_ID = uuid4()
BASE = f"/api/model/{MODEL_ID}/results-upload"
BUCKET = "s3://results-bucket/results"
ZIP = "20260101_120000.zip"
CREDENTIAL = "job-credential"  # pragma: allowlist secret
START = {"file_name": ZIP, "size": 1}
PART = {"file_name": ZIP, "upload_id": "upload-1", "part_number": 1}
COMPLETE = {"file_name": ZIP, "upload_id": "upload-1", "etags": ['"a"', '"b"']}
ROUTES = [("", START), ("/part", PART), ("/complete", COMPLETE)]


@pytest.fixture
def s3() -> Generator[MagicMock, None, None]:
    with (
        patch("flip_api.private_services.results_upload.S3Client") as s3_class,
        patch("flip_api.private_services.results_upload.get_settings") as settings,
    ):
        settings.return_value.UPLOADED_FEDERATED_DATA_BUCKET = BUCKET
        s3_class.return_value.create_multipart_upload.return_value = "upload-1"
        s3_class.return_value.get_upload_part_presigned_url.return_value = _FAKE_SIGNED_URL
        yield s3_class.return_value


@pytest.fixture
def client(s3: MagicMock) -> Generator[TestClient, None, None]:
    """A client whose model has one reporting job, holding ``CREDENTIAL``."""
    app.dependency_overrides[authenticate_internal_service] = lambda: None
    app.dependency_overrides[get_session] = lambda: MagicMock()
    job = MagicMock(credential_hash=hash_job_credential(CREDENTIAL))
    with patch("flip_api.auth.job_callback.reporting_jobs", return_value=[job]):
        yield TestClient(app, headers={JOB_CREDENTIAL_HEADER: CREDENTIAL})
    app.dependency_overrides = {}


def test_start_creates_the_upload_under_the_models_prefix(client: TestClient, s3: MagicMock) -> None:
    response = client.post(BASE, json={"file_name": ZIP, "size": 3 * RESULTS_UPLOAD_PART_BYTES})

    assert response.status_code == 200
    assert response.json() == {"upload_id": "upload-1", "part_size": RESULTS_UPLOAD_PART_BYTES}
    s3.create_multipart_upload.assert_called_once_with(f"{BUCKET}/{MODEL_ID}/{ZIP}")


def test_part_url_is_signed_for_the_fl_server_and_never_logged(
    client: TestClient, s3: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        response = client.post(f"{BASE}/part", json={**PART, "part_number": 7})

    assert response.status_code == 200
    assert response.json() == {"url": _FAKE_SIGNED_URL}
    s3.get_upload_part_presigned_url.assert_called_once_with(
        f"{BUCKET}/{MODEL_ID}/{ZIP}",
        "upload-1",
        7,
        audience=PresignAudience.INTERNAL,
        expiration=PART_URL_TTL_SECONDS,
    )
    _assert_logs_have_no_presigned_url(caplog.records)


def test_complete_passes_the_etags_in_part_order(client: TestClient, s3: MagicMock) -> None:
    response = client.post(f"{BASE}/complete", json=COMPLETE)

    assert response.status_code == 200
    s3.complete_multipart_upload.assert_called_once_with(f"{BUCKET}/{MODEL_ID}/{ZIP}", "upload-1", ['"a"', '"b"'])


@pytest.mark.parametrize(("path", "body"), ROUTES)
@pytest.mark.parametrize("credential", [None, "another-jobs-credential"])
def test_wrong_or_missing_credential_is_refused(
    client: TestClient, s3: MagicMock, path: str, body: dict, credential: str | None
) -> None:
    client.headers.pop(JOB_CREDENTIAL_HEADER)
    headers = {JOB_CREDENTIAL_HEADER: credential} if credential else {}

    response = client.post(BASE + path, json=body, headers=headers)

    assert response.status_code == 403
    assert response.json() == {"detail": "Not authorised for this model."}
    assert s3.mock_calls == []


@pytest.mark.parametrize(("path", "body"), ROUTES)
@pytest.mark.parametrize(
    "file_name",
    [
        "../other-model/results.zip",
        "other-model/results.zip",
        "..zip",
        "a.b.zip",
        "results.zip/",
        "results.tar",
        "results",
        ".zip",
        "a\\b.zip",
        "a b.zip",
        "a%2Fb.zip",
        "results.zip\n",
        "",
    ],
)
def test_file_name_must_be_a_plain_zip_name(
    client: TestClient, s3: MagicMock, path: str, body: dict, file_name: str
) -> None:
    response = client.post(BASE + path, json={**body, "file_name": file_name})

    assert response.status_code == 422
    assert s3.mock_calls == []


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("", {**START, "size": 0}),
        ("", {**START, "size": RESULTS_UPLOAD_MAX_PARTS * RESULTS_UPLOAD_PART_BYTES + 1}),
        ("/part", {**PART, "part_number": 0}),
        ("/part", {**PART, "part_number": RESULTS_UPLOAD_MAX_PARTS + 1}),
        ("/part", {**PART, "upload_id": ""}),
        ("/complete", {**COMPLETE, "etags": []}),
        ("/complete", {**COMPLETE, "etags": ["e"] * (RESULTS_UPLOAD_MAX_PARTS + 1)}),
    ],
)
def test_sizes_and_part_numbers_stay_within_the_multipart_limits(
    client: TestClient, s3: MagicMock, path: str, body: dict
) -> None:
    assert client.post(BASE + path, json=body).status_code == 422
    assert s3.mock_calls == []


@pytest.mark.parametrize(
    ("path", "body", "method", "detail"),
    [
        ("", START, "create_multipart_upload", "Unable to start the results upload."),
        ("/part", PART, "get_upload_part_presigned_url", "Unable to presign the results upload."),
        ("/complete", COMPLETE, "complete_multipart_upload", "Unable to complete the results upload."),
    ],
)
def test_object_store_failure_is_a_500_that_does_not_log_the_message(
    client: TestClient, s3: MagicMock, caplog: pytest.LogCaptureFixture, path: str, body: dict, method: str, detail: str
) -> None:
    error = ClientError({"Error": {"Code": "NoSuchUpload", "Message": _FAKE_SIGNED_URL}}, method)
    getattr(s3, method).side_effect = error

    with caplog.at_level(logging.DEBUG):
        response = client.post(BASE + path, json=body)

    assert response.status_code == 500
    assert response.json() == {"detail": detail}
    _assert_logs_have_no_presigned_url(caplog.records)
