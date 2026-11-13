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

"""The results-upload routes: a job's own credential, an upload the hub started for that model, its declared size."""

import logging
from collections.abc import Generator
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

from flip_api.auth.job_callback import JOB_CREDENTIAL_HEADER, hash_job_credential
from flip_api.config import Settings
from flip_api.db.database import get_session
from flip_api.domain.schemas.private import RESULTS_UPLOAD_MAX_PARTS, RESULTS_UPLOAD_PART_BYTES
from flip_api.main import app
from flip_api.private_services.results_upload import PART_URL_TTL_SECONDS
from flip_api.utils.encryption import encrypt
from flip_api.utils.s3_client import PresignAudience
from tests.unit._log_policy import _FAKE_SIGNED_URL, _assert_logs_have_no_presigned_url

MODEL_ID = uuid4()
BASE = f"/api/model/{MODEL_ID}/results-upload"
BUCKET = "s3://results-bucket/results"
ZIP = "20260101_120000.zip"
OBJECT = f"{BUCKET}/{MODEL_ID}/{ZIP}"
CREDENTIAL = "job-credential"  # pragma: allowlist secret
MAX_BYTES = 10 * RESULTS_UPLOAD_PART_BYTES
# Two full parts and a 5-byte third.
SIZE = 2 * RESULTS_UPLOAD_PART_BYTES + 5
ETAGS = ['"a"', '"b"', '"c"']


@pytest.fixture
def s3() -> Generator[MagicMock, None, None]:
    with (
        patch("flip_api.private_services.results_upload.S3Client") as s3_class,
        patch("flip_api.private_services.results_upload.get_settings") as settings,
    ):
        settings.return_value.UPLOADED_FEDERATED_DATA_BUCKET = BUCKET
        settings.return_value.MAX_RESULTS_FILE_BYTES = MAX_BYTES
        s3_class.return_value.abort_multipart_uploads.return_value = 0
        s3_class.return_value.create_multipart_upload.return_value = "upload-1"
        s3_class.return_value.get_upload_part_presigned_url.return_value = _FAKE_SIGNED_URL
        yield s3_class.return_value


@pytest.fixture
def client(s3: MagicMock) -> Generator[TestClient, None, None]:
    """A client whose model has one reporting job, holding ``CREDENTIAL``."""
    app.dependency_overrides[get_session] = lambda: MagicMock()
    job = MagicMock(credential_hash=hash_job_credential(CREDENTIAL))
    with patch("flip_api.auth.job_callback.reporting_jobs", return_value=[job]):
        yield TestClient(app, headers={JOB_CREDENTIAL_HEADER: CREDENTIAL})
    app.dependency_overrides = {}


@pytest.fixture
def upload(client: TestClient, s3: MagicMock) -> str:
    """The handle of a started upload of ``SIZE`` bytes."""
    handle = client.post(BASE, json={"file_name": ZIP, "size": SIZE}).json()["upload"]
    s3.reset_mock()
    return handle


def _bodies(upload: str) -> list[tuple[str, dict]]:
    return [
        ("/part", {"upload": upload, "part_number": 1}),
        ("/complete", {"upload": upload, "etags": ETAGS}),
        ("/abort", {"upload": upload}),
    ]


def test_start_creates_the_upload_under_the_models_prefix(client: TestClient, s3: MagicMock) -> None:
    response = client.post(BASE, json={"file_name": ZIP, "size": MAX_BYTES})

    assert response.status_code == 200
    assert response.json()["part_size"] == RESULTS_UPLOAD_PART_BYTES
    assert "upload-1" not in response.json()["upload"]
    s3.create_multipart_upload.assert_called_once_with(OBJECT)


def test_start_aborts_the_models_unfinished_uploads_first(client: TestClient, s3: MagicMock) -> None:
    client.post(BASE, json={"file_name": ZIP, "size": SIZE})

    assert [call[0] for call in s3.method_calls] == ["abort_multipart_uploads", "create_multipart_upload"]
    s3.abort_multipart_uploads.assert_called_once_with(f"{BUCKET}/{MODEL_ID}/")


def test_start_refuses_a_file_over_the_cap(client: TestClient, s3: MagicMock) -> None:
    response = client.post(BASE, json={"file_name": ZIP, "size": MAX_BYTES + 1})

    assert response.status_code == 413
    assert s3.mock_calls == []


def test_the_cap_setting_stays_within_what_the_parts_can_carry() -> None:
    assert Settings.model_fields["MAX_RESULTS_FILE_BYTES"].metadata[1].le == (
        RESULTS_UPLOAD_MAX_PARTS * RESULTS_UPLOAD_PART_BYTES
    )


@pytest.mark.parametrize(
    ("part_number", "length"), [(1, RESULTS_UPLOAD_PART_BYTES), (2, RESULTS_UPLOAD_PART_BYTES), (3, 5)]
)
def test_part_url_is_signed_for_the_fl_server_and_that_parts_size_and_never_logged(
    client: TestClient, s3: MagicMock, upload: str, caplog: pytest.LogCaptureFixture, part_number: int, length: int
) -> None:
    with caplog.at_level(logging.DEBUG):
        response = client.post(f"{BASE}/part", json={"upload": upload, "part_number": part_number})

    assert response.status_code == 200
    assert response.json() == {"url": _FAKE_SIGNED_URL}
    s3.get_upload_part_presigned_url.assert_called_once_with(
        OBJECT, "upload-1", part_number, length, audience=PresignAudience.INTERNAL, expiration=PART_URL_TTL_SECONDS
    )
    _assert_logs_have_no_presigned_url(caplog.records)


@pytest.mark.parametrize("part_number", [0, 4, RESULTS_UPLOAD_MAX_PARTS])
def test_part_beyond_the_declared_size_is_refused(
    client: TestClient, s3: MagicMock, upload: str, part_number: int
) -> None:
    response = client.post(f"{BASE}/part", json={"upload": upload, "part_number": part_number})

    assert response.status_code in (400, 422)
    assert s3.mock_calls == []


def test_complete_passes_the_etags_in_part_order(client: TestClient, s3: MagicMock, upload: str) -> None:
    response = client.post(f"{BASE}/complete", json={"upload": upload, "etags": ETAGS})

    assert response.status_code == 200
    s3.complete_multipart_upload.assert_called_once_with(OBJECT, "upload-1", ETAGS)
    s3.abort_multipart_upload.assert_not_called()


@pytest.mark.parametrize("etags", [ETAGS[:2], [*ETAGS, '"d"'], []])
def test_complete_refuses_a_part_count_other_than_the_declared_sizes(
    client: TestClient, s3: MagicMock, upload: str, etags: list[str]
) -> None:
    response = client.post(f"{BASE}/complete", json={"upload": upload, "etags": etags})

    assert response.status_code in (400, 422)
    assert s3.mock_calls == []


def test_an_upload_that_cannot_be_completed_is_aborted(client: TestClient, s3: MagicMock, upload: str) -> None:
    s3.complete_multipart_upload.side_effect = ClientError({"Error": {"Code": "InvalidPart"}}, "complete")

    response = client.post(f"{BASE}/complete", json={"upload": upload, "etags": ETAGS})

    assert response.status_code == 500
    s3.abort_multipart_upload.assert_called_once_with(OBJECT, "upload-1")


def test_abort_discards_the_upload(client: TestClient, s3: MagicMock, upload: str) -> None:
    response = client.post(f"{BASE}/abort", json={"upload": upload})

    assert response.status_code == 200
    s3.abort_multipart_upload.assert_called_once_with(OBJECT, "upload-1")


@pytest.mark.parametrize("route", [0, 1, 2])
@pytest.mark.parametrize(
    "forged",
    [
        "upload-1",
        "not-a-handle",
        # Sealed by the hub, but for another model or another purpose.
        lambda: encrypt('{"file_name":"a.zip","upload_id":"u","size":1}', context=f"results_upload:{uuid4()}"),
        lambda: encrypt('{"file_name":"a.zip","upload_id":"u","size":1}', context="project_id"),
    ],
)
def test_only_a_handle_the_hub_issued_for_this_model_is_acted_on(
    client: TestClient, s3: MagicMock, route: int, forged: object
) -> None:
    path, body = _bodies(forged() if callable(forged) else str(forged))[route]

    response = client.post(BASE + path, json=body)

    assert response.status_code == 400
    assert response.json() == {"detail": "Unknown upload."}
    assert s3.mock_calls == []


@pytest.mark.parametrize("route", [None, 0, 1, 2])
@pytest.mark.parametrize("credential", [None, "another-jobs-credential"])
def test_wrong_or_missing_credential_is_refused(
    client: TestClient, s3: MagicMock, upload: str, route: int | None, credential: str | None
) -> None:
    path, body = ("", {"file_name": ZIP, "size": SIZE}) if route is None else _bodies(upload)[route]
    client.headers.pop(JOB_CREDENTIAL_HEADER)
    headers = {JOB_CREDENTIAL_HEADER: credential} if credential else {}

    response = client.post(BASE + path, json=body, headers=headers)

    assert response.status_code == 403
    assert response.json() == {"detail": "Not authorised for this model."}
    assert s3.mock_calls == []


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
def test_file_name_must_be_a_plain_zip_name(client: TestClient, s3: MagicMock, file_name: str) -> None:
    response = client.post(BASE, json={"file_name": file_name, "size": SIZE})

    assert response.status_code == 422
    assert s3.mock_calls == []


def test_start_refuses_an_empty_file(client: TestClient, s3: MagicMock) -> None:
    assert client.post(BASE, json={"file_name": ZIP, "size": 0}).status_code == 422
    assert s3.mock_calls == []


@pytest.mark.parametrize(
    ("route", "method", "detail"),
    [
        (None, "abort_multipart_uploads", "Unable to start the results upload."),
        (None, "create_multipart_upload", "Unable to start the results upload."),
        (0, "get_upload_part_presigned_url", "Unable to presign the results upload."),
        (1, "complete_multipart_upload", "Unable to complete the results upload."),
        (2, "abort_multipart_upload", "Unable to abort the results upload."),
    ],
)
def test_object_store_failure_is_a_500_that_does_not_log_the_message(
    client: TestClient,
    s3: MagicMock,
    upload: str,
    caplog: pytest.LogCaptureFixture,
    route: int | None,
    method: str,
    detail: str,
) -> None:
    path, body = ("", {"file_name": ZIP, "size": SIZE}) if route is None else _bodies(upload)[route]
    getattr(s3, method).side_effect = ClientError({"Error": {"Code": "X", "Message": _FAKE_SIGNED_URL}}, method)

    with caplog.at_level(logging.DEBUG):
        response = client.post(BASE + path, json=body)

    assert response.status_code == 500
    assert response.json() == {"detail": detail}
    _assert_logs_have_no_presigned_url(caplog.records)
