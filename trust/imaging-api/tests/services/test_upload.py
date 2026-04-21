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

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from imaging_api.config import get_settings
from imaging_api.services.upload import (
    check_file_exists_in_xnat,
    create_xnat_resource,
    create_xnat_scan,
    upload_data_to_xnat,
    upload_file_to_xnat,
)
from imaging_api.utils.exceptions import AlreadyExistsError

XNAT_URL = get_settings().XNAT_URL


# ── create_xnat_scan ──


@patch("imaging_api.services.upload.requests.put")
def test_create_existing_xnat_scan(mock_put, headers):
    mock_put.return_value = MagicMock(status_code=200)

    create_xnat_scan(
        project_id="test",
        subject_id="XNAT_S00002",
        experiment_id_or_label="XNAT_E00002",
        scan_id="1_2_826_0_1_3680043_8_274_1_1_8323329_1189734_1740750875_622789",
        headers=headers,
    )


@patch("imaging_api.services.upload.requests.put")
def test_create_xnat_scan_failure(mock_put, headers):
    mock_put.return_value = MagicMock(status_code=500, text="Internal error")
    with pytest.raises(Exception, match="Error creating scan"):
        create_xnat_scan("PROJ", "SUBJ", "EXP", "SCAN1", headers)


# ── create_xnat_resource ──


@patch("imaging_api.services.upload.requests.put")
def test_create_existing_xnat_resource(mock_put, headers):
    mock_put.return_value = MagicMock(status_code=409)

    with pytest.raises(AlreadyExistsError) as exc_info:
        create_xnat_resource(
            project_id="test",
            subject_id="XNAT_S00002",
            experiment_id_or_label="XNAT_E00002",
            scan_id="1_2_826_0_1_3680043_8_274_1_1_8323329_1189734_1740750875_622789",
            resource_id="DICOM",
            headers=headers,
        )

    assert str(exc_info.value) == "Resource already exists: DICOM"


@patch("imaging_api.services.upload.requests.put")
def test_create_xnat_resource_non_200_non_409(mock_put, headers):
    mock_put.return_value = MagicMock(status_code=500, text="Server error")
    with pytest.raises(Exception, match="Error creating resource"):
        create_xnat_resource("PROJ", "SUBJ", "EXP", "SCAN1", "DICOM", headers)


@patch("imaging_api.services.upload.requests.put")
def test_create_xnat_resource_success(mock_put, headers):
    mock_put.return_value = MagicMock(status_code=200)
    create_xnat_resource("PROJ", "SUBJ", "EXP", "SCAN1", "DICOM", headers)


# ── check_file_exists_in_xnat ──


@patch("imaging_api.services.upload.requests.get")
def test_check_file_exists_true(mock_get, headers):
    mock_get.return_value = MagicMock(status_code=200)
    assert check_file_exists_in_xnat("http://xnat/file", headers) is True


@patch("imaging_api.services.upload.requests.get")
def test_check_file_exists_false(mock_get, headers):
    mock_get.return_value = MagicMock(status_code=404)
    assert check_file_exists_in_xnat("http://xnat/file", headers) is False


# ── upload_file_to_xnat ──


def test_upload_nonexistent_file_to_xnat(headers):
    file_path = "tests/data/test.dcm"

    with pytest.raises(FileNotFoundError) as exc_info:
        upload_file_to_xnat(
            project_id="test",
            subject_id="XNAT_S00002",
            experiment_id_or_label="XNAT_E00002",
            scan_id="1_2_826_0_1_3680043_8_274_1_1_8323329_1189734_1740750875_622789",
            resource_id="DICOM",
            file_path=file_path,
            exist_ok=True,
            headers=headers,
        )

    assert str(exc_info.value) == f"[Errno 2] No such file or directory: '{file_path}'"


@patch("imaging_api.services.upload.check_file_exists_in_xnat")
@patch("imaging_api.services.upload.requests.put")
def test_upload_file_to_xnat(mock_put, mock_check_file_exists_in_xnat, headers):
    mock_put.return_value = MagicMock(status_code=200)
    mock_check_file_exists_in_xnat.return_value = False

    with tempfile.NamedTemporaryFile(delete=False) as temp_file:
        temp_file.write(b"Hello, world!")
        temp_file_path = temp_file.name

    try:
        uploaded_file = upload_file_to_xnat(
            project_id="test",
            subject_id="XNAT_S00002",
            experiment_id_or_label="XNAT_E00002",
            scan_id="TEST_SCAN",
            resource_id="TEST_FILES",
            file_path=temp_file_path,
            exist_ok=False,
            headers=headers,
        )
        temp_file_name = os.path.basename(temp_file_path)
        assert (
            uploaded_file == f"{XNAT_URL}/data/projects/test/subjects/XNAT_S00002/"
            f"experiments/XNAT_E00002/scans/TEST_SCAN/"
            f"resources/TEST_FILES/files/{temp_file_name}?inbody=true"
        )
    finally:
        os.remove(temp_file_path)


@patch("imaging_api.services.upload.check_file_exists_in_xnat", return_value=True)
def test_upload_file_exist_ok_false_already_exists(mock_check, headers):
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(b"data")
        temp_path = f.name

    try:
        with pytest.raises(AlreadyExistsError, match="File already exists on XNAT"):
            upload_file_to_xnat(
                "PROJ", "SUBJ", "EXP", "SCAN1", "DICOM", temp_path, exist_ok=False, headers=headers
            )
    finally:
        os.remove(temp_path)


# ── upload_data_to_xnat ──


class TestUploadDataToXnat:
    @pytest.mark.asyncio
    @patch("imaging_api.services.upload.upload_file_to_xnat")
    @patch("imaging_api.services.upload.create_xnat_resource")
    @patch("imaging_api.services.upload.create_xnat_scan")
    @patch("imaging_api.services.upload.get_subject_id_from_experiment_response", return_value="SUBJ1")
    @patch("imaging_api.services.upload.get_experiment", return_value={})
    @patch("imaging_api.services.upload.get_project_from_central_hub_project_id")
    async def test_success(
        self, mock_get_project, mock_get_exp, mock_get_subj, mock_create_scan, mock_create_res, mock_upload, headers
    ):
        mock_get_project.return_value = MagicMock(ID="PROJ1")
        mock_upload.return_value = f"{XNAT_URL}/data/projects/PROJ1/file.nii"

        with tempfile.TemporaryDirectory() as tmp_dir:
            upload_dir = os.path.join(tmp_dir, "net1", "upload")
            os.makedirs(upload_dir)
            with open(os.path.join(upload_dir, "scan.nii"), "w") as f:
                f.write("nifti-data")

            with patch("imaging_api.services.upload.BASE_IMAGES_DOWNLOAD_DIR", tmp_dir):
                result = await upload_data_to_xnat(
                    central_hub_project_id="hub-proj-1",
                    accession_id="ACC123",
                    net_id="net1",
                    scan_id="SCAN1",
                    resource_id="NIFTI",
                    files_relative_paths_to_upload=["scan.nii"],
                    exist_ok=False,
                    headers=headers,
                )

            assert len(result) == 1

    @pytest.mark.asyncio
    async def test_file_not_found(self, headers):
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch("imaging_api.services.upload.BASE_IMAGES_DOWNLOAD_DIR", tmp_dir):
                with pytest.raises(FileNotFoundError, match="File not found"):
                    await upload_data_to_xnat(
                        central_hub_project_id="hub-proj-1",
                        accession_id="ACC123",
                        net_id="net1",
                        scan_id="SCAN1",
                        resource_id="NIFTI",
                        files_relative_paths_to_upload=["nonexistent.nii"],
                        exist_ok=False,
                        headers=headers,
                    )

    @pytest.mark.asyncio
    async def test_net_id_path_traversal_is_rejected(self, headers):
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch("imaging_api.services.upload.BASE_IMAGES_DOWNLOAD_DIR", tmp_dir):
                with pytest.raises(ValueError, match="Path traversal detected in net ID"):
                    await upload_data_to_xnat(
                        central_hub_project_id="hub-proj-1",
                        accession_id="ACC123",
                        net_id="../escape",
                        scan_id="SCAN1",
                        resource_id="NIFTI",
                        files_relative_paths_to_upload=["scan.nii"],
                        exist_ok=False,
                        headers=headers,
                    )

    @pytest.mark.asyncio
    @patch("imaging_api.services.upload.upload_file_to_xnat")
    @patch("imaging_api.services.upload.create_xnat_resource")
    @patch("imaging_api.services.upload.create_xnat_scan")
    @patch("imaging_api.services.upload.get_subject_id_from_experiment_response", return_value="SUBJ1")
    @patch("imaging_api.services.upload.get_experiment", return_value={})
    @patch("imaging_api.services.upload.get_project_from_central_hub_project_id")
    async def test_resource_already_exists_is_ignored(
        self, mock_get_project, mock_get_exp, mock_get_subj, mock_create_scan, mock_create_res, mock_upload, headers
    ):
        mock_get_project.return_value = MagicMock(ID="PROJ1")
        mock_create_res.side_effect = AlreadyExistsError("Resource already exists")
        mock_upload.return_value = "url"

        with tempfile.TemporaryDirectory() as tmp_dir:
            upload_dir = os.path.join(tmp_dir, "net1", "upload")
            os.makedirs(upload_dir)
            with open(os.path.join(upload_dir, "scan.nii"), "w") as f:
                f.write("data")

            with patch("imaging_api.services.upload.BASE_IMAGES_DOWNLOAD_DIR", tmp_dir):
                result = await upload_data_to_xnat(
                    central_hub_project_id="hub-proj-1",
                    accession_id="ACC123",
                    net_id="net1",
                    scan_id="SCAN1",
                    resource_id="NIFTI",
                    files_relative_paths_to_upload=["scan.nii"],
                    exist_ok=False,
                    headers=headers,
                )

            assert len(result) == 1
