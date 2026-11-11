# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
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

"""The layout route (FLIP#1857): a study's files named in XNAT's archive and in the download layout, no content."""

from unittest.mock import patch

from imaging_api.utils.exceptions import NotFoundError

_REQUEST_BODY = {"encrypted_central_hub_project_id": "encrypted-id", "accession_id": "ACC123"}
_FILE = {
    "path": "ACC123/scans/1-T1/resources/NIFTI/files/a.nii.gz",
    "archive": "arc001/ACC123/SCANS/1/NIFTI/a.nii.gz",
}


def test_layout_names_the_studys_files_for_the_opened_project(client):
    with (
        patch("imaging_api.routers.download.decrypt", return_value="decrypted-project-id"),
        patch("imaging_api.routers.download.study_layout", return_value=[_FILE]) as service,
    ):
        response = client.post("/download/images/net1/layout?resource_type=DICOM", json=_REQUEST_BODY)

    assert response.status_code == 200
    assert response.json() == {"files": [_FILE]}
    assert service.call_args.args[:3] == ("decrypted-project-id", "ACC123", "DICOM")


def test_layout_answers_404_for_a_study_xnat_does_not_have(client):
    with (
        patch("imaging_api.routers.download.decrypt", return_value="decrypted-project-id"),
        patch("imaging_api.routers.download.study_layout", side_effect=NotFoundError("gone")),
    ):
        response = client.post("/download/images/net1/layout", json=_REQUEST_BODY)

    assert response.status_code == 404


def test_layout_answers_500_when_xnat_cannot_be_listed(client):
    with (
        patch("imaging_api.routers.download.decrypt", return_value="decrypted-project-id"),
        patch("imaging_api.routers.download.study_layout", side_effect=RuntimeError("boom")),
    ):
        response = client.post("/download/images/net1/layout", json=_REQUEST_BODY)

    assert response.status_code == 500


def test_layout_refuses_an_accession_that_is_not_one_path_segment(client):
    response = client.post(
        "/download/images/net1/layout", json={"encrypted_central_hub_project_id": "x", "accession_id": "../other"}
    )

    assert response.status_code == 422
