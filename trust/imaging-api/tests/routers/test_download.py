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

from unittest.mock import AsyncMock, patch

from imaging_api.utils.exceptions import LocalStorageError, NotFoundError

_REQUEST_BODY = {
    "encrypted_central_hub_project_id": "encrypted-id",
    "accession_id": "ACC123",
}


def test_download_images_success(client):
    with (
        patch("imaging_api.routers.download.decrypt", return_value="decrypted-project-id"),
        patch(
            "imaging_api.routers.download.download_and_unzip_images",
            new_callable=AsyncMock,
            return_value="/tmp/images/net1/ACC123",
        ),
    ):
        response = client.post("/download/images/net1", json=_REQUEST_BODY)

    assert response.status_code == 200
    assert response.json()["path"] == "/tmp/images/net1/ACC123"


def test_download_images_not_found(client):
    with (
        patch("imaging_api.routers.download.decrypt", return_value="decrypted-project-id"),
        patch(
            "imaging_api.routers.download.download_and_unzip_images",
            new_callable=AsyncMock,
            side_effect=NotFoundError("Project not found"),
        ),
    ):
        response = client.post("/download/images/net1", json=_REQUEST_BODY)

    assert response.status_code == 404
    assert "Resource not found" in response.json()["detail"]


def test_download_images_invalid_request(client):
    with (
        patch("imaging_api.routers.download.decrypt", return_value="decrypted-project-id"),
        patch(
            "imaging_api.routers.download.download_and_unzip_images",
            new_callable=AsyncMock,
            side_effect=ValueError("Attempted path traversal in ZIP entry: ../evil.txt"),
        ),
    ):
        response = client.post("/download/images/net1", json=_REQUEST_BODY)

    assert response.status_code == 400
    assert "Invalid download request" in response.json()["detail"]


def test_download_images_decrypt_failure(client):
    with patch("imaging_api.routers.download.decrypt", side_effect=Exception("bad key")):
        response = client.post("/download/images/net1", json=_REQUEST_BODY)

    assert response.status_code == 500
    assert "Failed to decrypt" in response.json()["detail"]


def test_download_images_server_error(client):
    with (
        patch("imaging_api.routers.download.decrypt", return_value="decrypted-project-id"),
        patch(
            "imaging_api.routers.download.download_and_unzip_images",
            new_callable=AsyncMock,
            side_effect=Exception("disk full"),
        ),
    ):
        response = client.post("/download/images/net1", json=_REQUEST_BODY)

    assert response.status_code == 500
    assert "Failed to download and unzip" in response.json()["detail"]


def test_download_images_local_storage_error_returns_500_not_404(client):
    """Trust-side storage failures must surface as 500 with a storage-specific
    detail, never as a 404 with an XNAT URL — the latter sent us on a wild
    goose chase hunting for missing DICOMs when the real issue was a missing
    bind-mount subdirectory."""
    with (
        patch("imaging_api.routers.download.decrypt", return_value="decrypted-project-id"),
        patch(
            "imaging_api.routers.download.download_and_unzip_images",
            new_callable=AsyncMock,
            side_effect=LocalStorageError(
                "Cannot create image download directory '/app/data/images/net-1' on the trust host: "
                "[Errno 13] Permission denied"
            ),
        ),
    ):
        response = client.post("/download/images/net1", json=_REQUEST_BODY)

    assert response.status_code == 500
    body = response.json()["detail"]
    assert "Trust-side storage error" in body
    assert "Resource not found" not in body


def test_download_images_xnat_genuine_404_returns_404_not_500(client):
    """Symmetric counterpart to the local-storage test: a genuine XNAT empty
    response must surface as 404 with an XNAT-shaped detail, never as 500
    with a 'Trust-side storage error' message."""
    xnat_msg = (
        "No DICOM data in XNAT at "
        "http://xnat/data/projects/PROJ1/subjects/SUBJ1/experiments/EXP1/"
        "scans/ALL/resources/NIFTI/files?format=zip: 404 Not Found"
    )
    with (
        patch("imaging_api.routers.download.decrypt", return_value="decrypted-project-id"),
        patch(
            "imaging_api.routers.download.download_and_unzip_images",
            new_callable=AsyncMock,
            side_effect=NotFoundError(xnat_msg),
        ),
    ):
        response = client.post("/download/images/net1", json=_REQUEST_BODY)

    assert response.status_code == 404
    body = response.json()["detail"]
    assert "Resource not found" in body
    assert "No DICOM data in XNAT" in body
    assert "Trust-side storage error" not in body
