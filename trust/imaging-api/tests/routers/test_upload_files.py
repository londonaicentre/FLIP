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

"""The multipart upload (FLIP#1049): files staged under the net's upload folder for the upload, then removed."""

import asyncio
import os
from unittest.mock import patch

import pytest

from imaging_api.services import upload as upload_service

_FORM = {
    "encrypted_central_hub_project_id": "encrypted-id",
    "accession_id": "ACC123",
    "scan_id": "12",
    "resource_id": "NIFTI",
    "exist_ok": "true",
}


def test_upload_files_stages_the_files_for_the_upload_then_removes_them(client, tmp_path, monkeypatch):
    monkeypatch.setattr(upload_service, "BASE_IMAGES_DOWNLOAD_DIR", str(tmp_path))
    seen = {}

    async def fake_upload(**kwargs):
        seen.update(kwargs)
        [relative] = kwargs["files_relative_paths_to_upload"]
        seen["staged"] = (tmp_path / "net1" / "upload" / relative).read_bytes()
        return ["http://xnat/input.nii"]

    with (
        patch("imaging_api.routers.download.decrypt", return_value="decrypted-project-id"),
        patch("imaging_api.routers.upload.upload_data_to_xnat", side_effect=fake_upload),
    ):
        response = client.put("/upload/images/net1/files", data=_FORM, files=[("files", ("input.nii", b"bytes"))])

    assert response.status_code == 200
    assert response.json() == ["http://xnat/input.nii"]
    assert seen["central_hub_project_id"] == "decrypted-project-id"
    assert seen["scan_id"] == "12"
    assert seen["exist_ok"] is True
    assert seen["files_relative_paths_to_upload"][0].endswith("/input.nii")
    assert seen["staged"] == b"bytes"
    assert os.listdir(tmp_path / "net1" / "upload") == []


def test_upload_files_rejects_an_unsafe_scan_id_and_leaves_nothing_behind(client, tmp_path, monkeypatch):
    monkeypatch.setattr(upload_service, "BASE_IMAGES_DOWNLOAD_DIR", str(tmp_path))

    with patch("imaging_api.routers.upload.upload_data_to_xnat") as service:
        response = client.put(
            "/upload/images/net1/files", data={**_FORM, "scan_id": "../x"}, files=[("files", ("input.nii", b"b"))]
        )

    assert response.status_code == 422
    service.assert_not_called()
    assert os.listdir(tmp_path / "net1" / "upload") == []


def test_staging_refuses_a_net_id_that_leaves_the_images_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(upload_service, "BASE_IMAGES_DOWNLOAD_DIR", str(tmp_path / "images"))
    (tmp_path / "images").mkdir()

    with pytest.raises(ValueError, match="Path traversal"):
        asyncio.run(upload_service.stage_uploads("../elsewhere", []))

    assert not (tmp_path / "elsewhere").exists()
