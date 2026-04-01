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

from imaging_api.utils.exceptions import NotFoundError

_REQUEST_BODY = {
    "encrypted_central_hub_project_id": "encrypted-id",
    "accession_id": "ACC123",
    "scan_id": "SCAN1",
    "resource_id": "NIFTI",
    "files": ["scan.nii"],
    "exist_ok": False,
}


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
