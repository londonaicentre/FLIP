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

from unittest.mock import AsyncMock, MagicMock, patch

from cryptography.exceptions import InvalidTag

from imaging_api.utils.exceptions import NotFoundError

_REQUEST_BODY = {
    "encrypted_central_hub_project_id": "encrypted-id",
    "accession_id": "ACC123",
    "scan_id": "SCAN1",
    "resource_id": "NIFTI",
    "files": ["scan.nii"],
    "exist_ok": False,
}


def test_upload_data_rejects_a_project_id_that_fails_authentication(client):
    with patch("imaging_api.routers.upload.decrypt", side_effect=InvalidTag()):
        response = client.put("/upload/images/net1", json=_REQUEST_BODY)

    assert response.status_code == 400
    assert "failed authentication" in response.json()["detail"]


def test_upload_data_rejects_a_malformed_envelope(client):
    with patch(
        "imaging_api.routers.upload.decrypt", side_effect=ValueError("Payload is not a FLIP encryption envelope")
    ):
        response = client.put("/upload/images/net1", json=_REQUEST_BODY)

    assert response.status_code == 400
    assert "not a FLIP encryption envelope" in response.json()["detail"]


def test_upload_data_success(client):
    with (
        patch("imaging_api.routers.upload.decrypt", return_value="decrypted-project-id"),
        patch(
            "imaging_api.routers.upload.upload_data_to_xnat",
            new_callable=AsyncMock,
            return_value=["http://xnat/file1.nii"],
        ),
    ):
        response = client.put("/upload/images/net1", json=_REQUEST_BODY)

    assert response.status_code == 200
    assert response.json() == ["http://xnat/file1.nii"]


def test_upload_data_decrypt_failure(client):
    with patch("imaging_api.routers.upload.decrypt", side_effect=Exception("bad key")):
        response = client.put("/upload/images/net1", json=_REQUEST_BODY)

    assert response.status_code == 500
    assert "Failed to decrypt" in response.json()["detail"]


def test_upload_data_rejects_accession_id_traversal_before_service_call(client):
    """A traversal accession_id must fail body validation (422) and never reach
    the upload service, which would issue an admin-authenticated XNAT request."""
    body = {**_REQUEST_BODY, "accession_id": "../../etc/passwd"}
    with patch(
        "imaging_api.routers.upload.upload_data_to_xnat",
        new_callable=AsyncMock,
    ) as mock_service:
        response = client.put("/upload/images/net1", json=body)

    assert response.status_code == 422
    mock_service.assert_not_called()


def test_upload_data_rejects_scan_id_traversal_before_service_call(client):
    """A traversal scan_id must fail body validation (422) and never reach the
    upload service, which would issue an admin-authenticated XNAT request."""
    body = {**_REQUEST_BODY, "scan_id": "../../OTHER"}
    with patch(
        "imaging_api.routers.upload.upload_data_to_xnat",
        new_callable=AsyncMock,
    ) as mock_service:
        response = client.put("/upload/images/net1", json=body)

    assert response.status_code == 422
    mock_service.assert_not_called()


def test_upload_data_rejects_resource_id_traversal_before_service_call(client):
    """A traversal resource_id must fail body validation (422) and never reach
    the upload service."""
    body = {**_REQUEST_BODY, "resource_id": "../../OTHER"}
    with patch(
        "imaging_api.routers.upload.upload_data_to_xnat",
        new_callable=AsyncMock,
    ) as mock_service:
        response = client.put("/upload/images/net1", json=body)

    assert response.status_code == 422
    mock_service.assert_not_called()


def test_upload_data_not_found(client):
    with (
        patch("imaging_api.routers.upload.decrypt", return_value="decrypted-project-id"),
        patch(
            "imaging_api.routers.upload.upload_data_to_xnat",
            new_callable=AsyncMock,
            side_effect=NotFoundError("Project not found"),
        ),
    ):
        response = client.put("/upload/images/net1", json=_REQUEST_BODY)

    assert response.status_code == 404
    assert "Resource not found" in response.json()["detail"]


def test_upload_data_invalid_request(client):
    with (
        patch("imaging_api.routers.upload.decrypt", return_value="decrypted-project-id"),
        patch(
            "imaging_api.routers.upload.upload_data_to_xnat",
            new_callable=AsyncMock,
            side_effect=ValueError("Path traversal detected in net ID: ../escape"),
        ),
    ):
        response = client.put("/upload/images/net1", json=_REQUEST_BODY)

    assert response.status_code == 400
    assert "Invalid upload request" in response.json()["detail"]


def test_upload_data_server_error(client):
    with (
        patch("imaging_api.routers.upload.decrypt", return_value="decrypted-project-id"),
        patch(
            "imaging_api.routers.upload.upload_data_to_xnat",
            new_callable=AsyncMock,
            side_effect=Exception("upload failed"),
        ),
    ):
        response = client.put("/upload/images/net1", json=_REQUEST_BODY)

    assert response.status_code == 500
    assert "Failed to upload files" in response.json()["detail"]


def test_upload_data_rejected_xnat_put_is_an_error_not_a_url(client, tmp_path):
    # End to end through the real service: a file PUT that XNAT rejects must surface as an error,
    # never as a URL in the success list (#1387).
    upload_dir = tmp_path / "net1" / "upload"
    upload_dir.mkdir(parents=True)
    (upload_dir / "scan.nii").write_bytes(b"nifti-data")

    with (
        patch("imaging_api.routers.upload.decrypt", return_value="decrypted-project-id"),
        patch("imaging_api.services.upload.BASE_IMAGES_DOWNLOAD_DIR", str(tmp_path)),
        patch("imaging_api.services.upload.get_project_from_central_hub_project_id", return_value=MagicMock(ID="P")),
        patch("imaging_api.services.upload.get_experiment", return_value={}),
        patch("imaging_api.services.upload.get_subject_id_from_experiment_response", return_value="SUBJ1"),
        patch("imaging_api.services.upload.create_xnat_scan"),
        patch("imaging_api.services.upload.create_xnat_resource"),
        patch("imaging_api.services.upload.check_file_exists_in_xnat", return_value=False),
        patch(
            "imaging_api.services.upload.requests.put",
            return_value=MagicMock(status_code=403, ok=False, text="Forbidden"),
        ),
    ):
        response = client.put("/upload/images/net1", json=_REQUEST_BODY)

    assert response.status_code == 500
    assert response.json()["detail"].startswith("Failed to upload files: Error uploading file")
