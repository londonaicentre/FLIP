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
from imaging_api.utils.exceptions import LocalStorageError, NotFoundError

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

    def test_zip_slip_entry_raises_value_error(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            zip_path = os.path.join(tmp_dir, "malicious.zip")

            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("../evil.txt", "bad")

            with pytest.raises(ValueError, match="Attempted path traversal in ZIP entry"):
                unzip_file(zip_path, tmp_dir, "ACC123")

            assert os.path.exists(zip_path)


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
        mock_download.side_effect = NotFoundError("No data found at: http://xnat/...")

        # XNAT 404 must surface as NotFoundError with a message that makes it
        # clear the remote URL is empty — not confused with a local-fs error.
        with pytest.raises(NotFoundError, match="No DICOM data in XNAT"):
            await download_and_unzip_images("hub-proj-1", "ACC1", "net1", "scan", "NIFTI", headers)

    @pytest.mark.asyncio
    @patch("imaging_api.services.download.download_file")
    @patch("imaging_api.services.download.get_subject_id_from_experiment_response", return_value="SUBJ1")
    @patch("imaging_api.services.download.get_experiment", return_value={})
    @patch("imaging_api.services.download.get_project_from_central_hub_project_id")
    async def test_local_storage_error_propagates_unwrapped(
        self, mock_get_project, mock_get_exp, mock_get_subj, mock_download, headers
    ):
        """A local-fs failure inside download_file must NOT be reported as 'not
        found at {xnat_url}' — that misled us into chasing XNAT when the real
        cause was a missing trust-side bind mount."""
        mock_get_project.return_value = MagicMock(ID="PROJ1")
        mock_download.side_effect = LocalStorageError("disk full on trust host")

        with pytest.raises(LocalStorageError, match="disk full on trust host"):
            await download_and_unzip_images("hub-proj-1", "ACC1", "net1", "scan", "NIFTI", headers)

    @pytest.mark.asyncio
    async def test_net_id_path_traversal_is_rejected(self, headers):
        with patch("imaging_api.services.download.BASE_IMAGES_DOWNLOAD_DIR", "/tmp/base"):
            with pytest.raises(ValueError, match="Path traversal detected in net ID"):
                await download_and_unzip_images("hub-proj-1", "ACC1", "../escape", "scan", "NIFTI", headers)

    @pytest.mark.asyncio
    @patch("imaging_api.services.download.get_subject_id_from_experiment_response", return_value="SUBJ1")
    @patch("imaging_api.services.download.get_experiment", return_value={})
    @patch("imaging_api.services.download.get_project_from_central_hub_project_id")
    async def test_accession_id_path_traversal_is_rejected(
        self, mock_get_project, mock_get_exp, mock_get_subj, headers, tmp_path
    ):
        """accession_id is user-controlled; a value like '../../etc/passwd' must
        never be allowed to escape the net download directory via the zip path."""
        mock_get_project.return_value = MagicMock(ID="PROJ1")
        net_dir = tmp_path / "net1"
        net_dir.mkdir()
        with patch("imaging_api.services.download.BASE_IMAGES_DOWNLOAD_DIR", str(tmp_path)):
            with pytest.raises(ValueError, match="Path traversal detected in accession_id"):
                await download_and_unzip_images(
                    "hub-proj-1", "../../etc/passwd", "net1", "scan", "NIFTI", headers
                )

    @pytest.mark.asyncio
    @patch("imaging_api.services.download.download_file")
    @patch("imaging_api.services.download.get_subject_id_from_experiment_response", return_value="SUBJ1")
    @patch("imaging_api.services.download.get_experiment", return_value={})
    @patch("imaging_api.services.download.get_project_from_central_hub_project_id")
    @patch("imaging_api.services.download.unzip_file")
    async def test_net_id_directory_is_created_on_demand(
        self, mock_unzip, mock_get_project, mock_get_exp, mock_get_subj, mock_download, headers
    ):
        """First-use of a net_id in a trust without the subdir pre-provisioned
        must work — imaging-api creates it rather than 404-ing."""
        mock_get_project.return_value = MagicMock(ID="PROJ1")
        with tempfile.TemporaryDirectory() as tmp:
            expected_net_dir = os.path.join(tmp, "net-never-seen-before")
            mock_download.return_value = os.path.join(expected_net_dir, "ACC1-scans-NIFTI.zip")
            mock_unzip.return_value = os.path.join(expected_net_dir, "ACC1")

            with patch("imaging_api.services.download.BASE_IMAGES_DOWNLOAD_DIR", tmp):
                await download_and_unzip_images(
                    "hub-proj-1", "ACC1", "net-never-seen-before", "scan", "NIFTI", headers
                )

            assert os.path.isdir(expected_net_dir), (
                f"download_and_unzip_images should have created {expected_net_dir}"
            )

    @pytest.mark.asyncio
    @patch("imaging_api.services.download.os.makedirs", side_effect=PermissionError("read-only fs"))
    async def test_net_dir_creation_failure_raises_local_storage_error(self, mock_makedirs, headers):
        """If the trust host can't create the net_id subdir at all (read-only
        FS, perms on the bind-mount root, etc.), surface it as
        LocalStorageError with a pointer to the offending path — not as a 404
        with an XNAT URL."""
        with pytest.raises(LocalStorageError, match="Cannot create image download directory"):
            await download_and_unzip_images(
                "hub-proj-1", "ACC1", "some-net", "scan", "NIFTI", headers
            )


class TestDownloadFileLocalWriteFailure:
    """download_file must distinguish XNAT-side 404 from local-fs write failures."""

    def _fake_200_response(self) -> MagicMock:
        response = MagicMock()
        response.status_code = 200
        response.iter_content = MagicMock(return_value=[b"payload"])
        return response

    @patch("imaging_api.services.download.requests.get")
    def test_unwritable_parent_raises_local_storage_error(self, mock_get, tmp_path):
        mock_get.return_value = self._fake_200_response()
        # Read-only parent dir => open() raises PermissionError (OSError subclass).
        parent = tmp_path / "readonly"
        parent.mkdir()
        parent.chmod(0o500)
        try:
            destination = parent / "out.zip"
            with pytest.raises(LocalStorageError, match="Failed to write downloaded file"):
                download_file("http://xnat/fake", str(destination), {})
        finally:
            parent.chmod(0o700)  # restore so tmp_path cleanup succeeds

    @patch("imaging_api.services.download.os.makedirs", side_effect=PermissionError("mount stale"))
    @patch("imaging_api.services.download.requests.get")
    def test_makedirs_failure_raises_local_storage_error(self, mock_get, mock_makedirs):
        """If the parent dir can't be created in `download_file` itself (e.g.
        defense-in-depth path when the caller didn't pre-create it), the error
        is reported as LocalStorageError, not as an XNAT miss."""
        mock_get.return_value = self._fake_200_response()

        with pytest.raises(LocalStorageError, match="Cannot create parent directory"):
            download_file("http://xnat/fake", "/nonexistent/parent/out.zip", {})

    @patch("imaging_api.services.download.requests.get")
    def test_xnat_404_still_raises_not_found_error(self, mock_get):
        response = MagicMock()
        response.status_code = 404
        mock_get.return_value = response

        with pytest.raises(NotFoundError, match="No data found at"):
            download_file("http://xnat/missing", "/tmp/irrelevant.zip", {})
