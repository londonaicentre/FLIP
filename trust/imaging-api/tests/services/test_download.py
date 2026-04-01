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
import shutil
import tempfile
import zipfile
from unittest.mock import MagicMock, patch

import pytest

from imaging_api.config import get_settings
from imaging_api.services.download import (
    download_and_unzip_images,
    download_file,
    format_download_url,
    unzip_file,
)
from imaging_api.utils.exceptions import NotFoundError

XNAT_URL = get_settings().XNAT_URL


# ── format_download_url ──


class TestFormatDownloadUrl:
    def test_scan_type(self):
        url = format_download_url("PROJ1", "SUBJ1", "EXP1", assessor_type="scan", resource_type="NIFTI")
        expected = (
            f"{XNAT_URL}/data/projects/PROJ1/subjects/SUBJ1/"
            "experiments/EXP1/scans/ALL/resources/NIFTI/files?format=zip"
        )
        assert url == expected

    def test_assessor_type(self):
        url = format_download_url("PROJ1", "SUBJ1", "EXP1", assessor_type="assessor", resource_type="DICOM")
        assert "/assessors/ALL/resources/DICOM/" in url

    def test_invalid_type_raises_assertion_error(self):
        with pytest.raises(AssertionError, match="Type must be 'scan' or 'assessor'"):
            format_download_url("PROJ1", "SUBJ1", "EXP1", assessor_type="invalid")

    def test_case_insensitive_type(self):
        url = format_download_url("PROJ1", "SUBJ1", "EXP1", assessor_type="SCAN")
        assert "/scans/ALL/" in url


# ── download_file ──


class TestDownloadFile:
    @patch("imaging_api.services.download.requests.get")
    def test_success(self, mock_get):
        mock_response = MagicMock(status_code=200)
        mock_response.iter_content.return_value = [b"chunk1", b"chunk2"]
        mock_get.return_value = mock_response

        with tempfile.TemporaryDirectory() as tmp_dir:
            dest = os.path.join(tmp_dir, "test.zip")
            result = download_file("http://example.com/file.zip", dest, {})
            assert result == dest
            assert os.path.exists(dest)
            with open(dest, "rb") as f:
                assert f.read() == b"chunk1chunk2"

    @patch("imaging_api.services.download.requests.get")
    def test_404_raises_not_found_error(self, mock_get):
        mock_get.return_value = MagicMock(status_code=404)
        with pytest.raises(NotFoundError, match="No data found"):
            download_file("http://example.com/missing.zip", "/tmp/missing.zip", {})

    @patch("imaging_api.services.download.requests.get")
    def test_server_error_raises_exception(self, mock_get):
        mock_get.return_value = MagicMock(status_code=500, text="Internal Server Error")
        with pytest.raises(Exception, match="Failed to download file"):
            download_file("http://example.com/error.zip", "/tmp/error.zip", {})


# ── unzip_file ──


class TestUnzipFile:
    def test_success(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Create a zip file with a directory inside
            zip_name = "test-scans-NIFTI"
            zip_path = os.path.join(tmp_dir, f"{zip_name}.zip")
            inner_dir = os.path.join(tmp_dir, zip_name)
            os.makedirs(inner_dir)
            with open(os.path.join(inner_dir, "scan.nii"), "w") as f:
                f.write("nifti-data")

            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.write(os.path.join(inner_dir, "scan.nii"), f"{zip_name}/scan.nii")

            shutil.rmtree(inner_dir)

            result = unzip_file(zip_path, tmp_dir, "ACC123")

            assert result == os.path.join(tmp_dir, "ACC123")
            assert os.path.exists(os.path.join(result, "scan.nii"))
            assert not os.path.exists(zip_path)  # zip deleted

    def test_not_found_raises_file_not_found_error(self):
        with pytest.raises(FileNotFoundError, match="ZIP file not found"):
            unzip_file("/nonexistent/path.zip", "/tmp", "test")


# ── download_and_unzip_images ──


class TestDownloadAndUnzipImages:
    @pytest.mark.asyncio
    @patch("imaging_api.services.download.unzip_file", return_value="/tmp/images/net1/ACC123")
    @patch("imaging_api.services.download.download_file", return_value="/tmp/images/net1/ACC123-scans-NIFTI.zip")
    @patch("imaging_api.services.download.get_subject_id_from_experiment_response", return_value="SUBJ1")
    @patch(
        "imaging_api.services.download.get_experiment",
        return_value={"items": [{"data_fields": {"subject_ID": "SUBJ1"}}]},
    )
    @patch("imaging_api.services.download.get_project_from_central_hub_project_id")
    @patch("imaging_api.services.download.os.walk", return_value=[])
    async def test_success(
        self, mock_walk, mock_get_project, mock_get_exp, mock_get_subj, mock_download, mock_unzip, headers
    ):
        mock_get_project.return_value = MagicMock(ID="PROJ1")

        result = await download_and_unzip_images(
            central_hub_project_id="hub-proj-1",
            accession_id="ACC123",
            net_id="net1",
            assessor_type="scan",
            resource_type="NIFTI",
            headers=headers,
        )

        assert result == "/tmp/images/net1/ACC123"
        mock_get_project.assert_called_once_with("hub-proj-1", headers)

    @pytest.mark.asyncio
    @patch("imaging_api.services.download.get_project_from_central_hub_project_id")
    async def test_project_not_found(self, mock_get_project, headers):
        mock_get_project.side_effect = NotFoundError("Project not found")

        with pytest.raises(NotFoundError, match="Project with ID"):
            await download_and_unzip_images("bad-id", "ACC1", "net1", "scan", "NIFTI", headers)

    @pytest.mark.asyncio
    @patch("imaging_api.services.download.get_experiment")
    @patch("imaging_api.services.download.get_project_from_central_hub_project_id")
    async def test_experiment_not_found(self, mock_get_project, mock_get_exp, headers):
        mock_get_project.return_value = MagicMock(ID="PROJ1")
        mock_get_exp.side_effect = NotFoundError("Experiment not found")

        with pytest.raises(NotFoundError, match="Experiment with ID or label"):
            await download_and_unzip_images("hub-proj-1", "BAD-ACC", "net1", "scan", "NIFTI", headers)

    @pytest.mark.asyncio
    @patch("imaging_api.services.download.get_project_from_central_hub_project_id")
    async def test_project_fetch_generic_error(self, mock_get_project, headers):
        mock_get_project.side_effect = Exception("connection refused")

        with pytest.raises(Exception, match="Failed to fetch project ID"):
            await download_and_unzip_images("hub-proj-1", "ACC1", "net1", "scan", "NIFTI", headers)

    @pytest.mark.asyncio
    @patch("imaging_api.services.download.get_experiment")
    @patch("imaging_api.services.download.get_project_from_central_hub_project_id")
    async def test_experiment_fetch_generic_error(self, mock_get_project, mock_get_exp, headers):
        mock_get_project.return_value = MagicMock(ID="PROJ1")
        mock_get_exp.side_effect = Exception("connection refused")

        with pytest.raises(Exception, match="Failed to fetch experiment details"):
            await download_and_unzip_images("hub-proj-1", "ACC1", "net1", "scan", "NIFTI", headers)

    @pytest.mark.asyncio
    @patch("imaging_api.services.download.download_file")
    @patch("imaging_api.services.download.get_subject_id_from_experiment_response", return_value="SUBJ1")
    @patch("imaging_api.services.download.get_experiment", return_value={})
    @patch("imaging_api.services.download.get_project_from_central_hub_project_id")
    async def test_download_failure(self, mock_get_project, mock_get_exp, mock_get_subj, mock_download, headers):
        mock_get_project.return_value = MagicMock(ID="PROJ1")
        mock_download.side_effect = Exception("disk full")

        with pytest.raises(Exception, match="Failed to download file"):
            await download_and_unzip_images("hub-proj-1", "ACC1", "net1", "scan", "NIFTI", headers)

    @pytest.mark.asyncio
    @patch("imaging_api.services.download.download_file")
    @patch("imaging_api.services.download.get_subject_id_from_experiment_response", return_value="SUBJ1")
    @patch("imaging_api.services.download.get_experiment", return_value={})
    @patch("imaging_api.services.download.get_project_from_central_hub_project_id")
    async def test_download_not_found(self, mock_get_project, mock_get_exp, mock_get_subj, mock_download, headers):
        mock_get_project.return_value = MagicMock(ID="PROJ1")
        mock_download.side_effect = NotFoundError("File not found")

        with pytest.raises(NotFoundError, match="File not found at"):
            await download_and_unzip_images("hub-proj-1", "ACC1", "net1", "scan", "NIFTI", headers)

    @pytest.mark.asyncio
    @patch("imaging_api.services.download.download_file")
    @patch("imaging_api.services.download.get_subject_id_from_experiment_response", return_value="SUBJ1")
    @patch("imaging_api.services.download.get_experiment", return_value={})
    @patch("imaging_api.services.download.get_project_from_central_hub_project_id")
    async def test_download_file_not_found_error(
        self, mock_get_project, mock_get_exp, mock_get_subj, mock_download, headers
    ):
        mock_get_project.return_value = MagicMock(ID="PROJ1")
        mock_download.side_effect = FileNotFoundError("No such file")

        with pytest.raises(NotFoundError, match="File not found at"):
            await download_and_unzip_images("hub-proj-1", "ACC1", "net1", "scan", "NIFTI", headers)
