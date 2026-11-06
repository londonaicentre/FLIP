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

"""The tar stream (FLIP#1049): the cached study, rooted at the accession, without the cache's sentinels."""

import io
import tarfile
from unittest.mock import AsyncMock, patch

from imaging_api.services.image_cache import SENTINEL_PREFIX
from imaging_api.utils.exceptions import NotFoundError

_REQUEST_BODY = {"encrypted_central_hub_project_id": "encrypted-id", "accession_id": "ACC123"}


def _cached_study(tmp_path):
    files = tmp_path / "project" / "ACC123" / "scans" / "1-T1" / "resources" / "NIFTI" / "files"
    files.mkdir(parents=True)
    (files / "a.nii.gz").write_bytes(b"nifti")
    (files / "b.nii.gz").write_bytes(b"x" * 1000)
    (tmp_path / "project" / "ACC123" / f"{SENTINEL_PREFIX}scan-NIFTI").write_text("{}")
    return tmp_path / "project" / "ACC123"


def test_stream_images_returns_the_study_as_a_tar_without_sentinels(client, tmp_path):
    study = _cached_study(tmp_path)
    with (
        patch("imaging_api.routers.download.decrypt", return_value="decrypted-project-id"),
        patch(
            "imaging_api.routers.download.download_and_unzip_images", new_callable=AsyncMock, return_value=str(study)
        ) as service,
    ):
        response = client.post("/download/images/net1/stream?resource_type=DICOM", json=_REQUEST_BODY)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/x-tar"
    assert service.call_args.kwargs["resource_type"] == "DICOM"
    with tarfile.open(fileobj=io.BytesIO(response.content)) as tar:
        members = {m.name: tar.extractfile(m).read() for m in tar.getmembers()}
    assert members == {
        "ACC123/scans/1-T1/resources/NIFTI/files/a.nii.gz": b"nifti",
        "ACC123/scans/1-T1/resources/NIFTI/files/b.nii.gz": b"x" * 1000,
    }


def test_stream_images_maps_errors_like_the_download_route(client):
    with (
        patch("imaging_api.routers.download.decrypt", return_value="decrypted-project-id"),
        patch(
            "imaging_api.routers.download.download_and_unzip_images",
            new_callable=AsyncMock,
            side_effect=NotFoundError("gone"),
        ),
    ):
        response = client.post("/download/images/net1/stream", json=_REQUEST_BODY)

    assert response.status_code == 404
