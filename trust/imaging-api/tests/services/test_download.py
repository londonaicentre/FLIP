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

import asyncio
import logging
import os
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from imaging_api.config import get_settings
from imaging_api.services.download import (
    download_and_unzip_images,
    download_file,
    format_download_url,
    get_resource_stats,
    unzip_file,
)
from imaging_api.services.image_cache import CachedDownload, read_sentinel, write_sentinel
from imaging_api.utils.exceptions import LocalStorageError, NotFoundError, XnatFetchError

XNAT_URL = get_settings().XNAT_URL

EXPERIMENT_RESPONSE = {"items": [{"data_fields": {"ID": "XNAT_E00001", "subject_ID": "SUBJ1"}}]}
STATS = [["173680", "NIFTI", "82", "1", "183823"]]


@pytest.fixture(autouse=True)
def resource_stats():
    """Stands in for XNAT's resource listing; tests of get_resource_stats import the real function."""
    with patch("imaging_api.services.download.get_resource_stats", return_value=STATS) as mock:
        yield mock


# ── format_download_url ──


class TestFormatDownloadUrl:
    def test_scan_type(self):
        url = format_download_url("PROJ1", "SUBJ1", "EXP1", assessor_type="scan", resource_type="NIFTI")
        expected = (
            f"{XNAT_URL}/data/projects/PROJ1/subjects/SUBJ1/experiments/EXP1/scans/ALL/resources/NIFTI/files"
            "?format=zip&structure=default"
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

    def test_all_resource_type_drops_resources_segment(self):
        """ALL means every resource: XNAT has no literal ALL resource label (404), so the
        URL must target scans/ALL/files directly — this also serves DICOM series whose
        resource label isn't "DICOM" (e.g. "secondary" for Secondary Capture)."""
        url = format_download_url("PROJ1", "SUBJ1", "EXP1", assessor_type="scan", resource_type="ALL")
        expected = (
            f"{XNAT_URL}/data/projects/PROJ1/subjects/SUBJ1/experiments/EXP1/scans/ALL/files"
            "?format=zip&structure=default"
        )
        assert url == expected

    def test_all_resource_type_is_case_insensitive(self):
        url = format_download_url("PROJ1", "SUBJ1", "EXP1", assessor_type="scan", resource_type="all")
        assert "/resources/" not in url


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


# The first member of a real `structure=default` zip from dev trust 1, with the label swapped.
XNAT_DEFAULT_MEMBER = "ACC123/scans/173680-unknown/resources/NIFTI/files/input_Chest_HCT_173680.nii.gz"


def _write_zip(path, members):
    """Writes a zip of the given member names; a name ending in "/" is a directory entry."""
    with zipfile.ZipFile(path, "w") as zf:
        for name in members:
            zf.writestr(name, "" if name.endswith("/") else f"data:{name}")
    return str(path)


class TestUnzipFile:
    def test_extracts_xnat_default_layout_into_accession_dir(self, tmp_path):
        zip_path = _write_zip(tmp_path / "ACC123-scans-NIFTI.zip", ["ACC123/", XNAT_DEFAULT_MEMBER])

        assert unzip_file(zip_path, str(tmp_path), "ACC123") == str(tmp_path / "ACC123")
        assert (tmp_path / XNAT_DEFAULT_MEMBER).is_file()
        assert not os.path.exists(zip_path)

    @pytest.mark.parametrize(
        "members",
        [
            ["scans/173680_unknown/NIFTI/input.nii.gz"],  # XNAT's `improved` layout: no session folder
            ["ACC123-scans-NIFTI/scan.nii"],  # rooted at the zip stem
            [XNAT_DEFAULT_MEMBER, "OTHER/scans/1/x.nii.gz"],  # one stray member
            ["OTHER/../ACC123/scans/1/x.nii.gz"],  # zipfile would write it to OTHER/ACC123/...
            ["ACC123/../OTHER/x.nii.gz"],
            ["ACC123"],  # a file, not a folder
            [],
            ["ACC123/"],
        ],
    )
    def test_refuses_anything_but_files_under_the_accession(self, tmp_path, members):
        (tmp_path / "OTHER").mkdir()
        zip_path = _write_zip(tmp_path / "ACC123-scans-NIFTI.zip", members)

        with pytest.raises(XnatFetchError, match="must hold files under ACC123/ only"):
            unzip_file(zip_path, str(tmp_path), "ACC123")

        assert sorted(os.listdir(tmp_path)) == ["ACC123-scans-NIFTI.zip", "OTHER"]
        assert os.listdir(tmp_path / "OTHER") == []

    def test_refuses_a_mislaid_zip_over_a_populated_accession_dir(self, tmp_path):
        """An existence check after extraction would pass here: the folder is there from the last download."""
        (tmp_path / XNAT_DEFAULT_MEMBER).parent.mkdir(parents=True)
        zip_path = _write_zip(tmp_path / "ACC123-scans-NIFTI.zip", ["scans/173680_unknown/NIFTI/input.nii.gz"])

        with pytest.raises(XnatFetchError):
            unzip_file(zip_path, str(tmp_path), "ACC123")

        assert not (tmp_path / "scans").exists()

    def test_a_member_through_an_on_disk_symlink_is_refused(self, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        (tmp_path / "proj" / "ACC123").mkdir(parents=True)
        (tmp_path / "proj" / "ACC123" / "scans").symlink_to(outside)
        zip_path = _write_zip(tmp_path / "proj" / "z.zip", ["ACC123/scans/x.nii.gz"])

        with pytest.raises(ValueError, match="Attempted path traversal in ZIP entry"):
            unzip_file(zip_path, str(tmp_path / "proj"), "ACC123")

        assert list(outside.iterdir()) == []

    @pytest.mark.parametrize("expected_root", ["../etc/passwd", "foo/../../etc/passwd"])
    def test_expected_root_path_traversal_raises_value_error(self, tmp_path, expected_root):
        zip_path = _write_zip(tmp_path / "ACC123-scans-NIFTI.zip", [XNAT_DEFAULT_MEMBER])

        with pytest.raises(ValueError, match="Path traversal detected in expected root"):
            unzip_file(zip_path, str(tmp_path), expected_root)

    def test_real_xnat_layout_merges_into_populated_accession_dir(self):
        """Re-extracting over a populated accession dir merges member by member; files absent from the
        new zip are left behind (#1026)."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            accession_dir = os.path.join(tmp_dir, "ACC123")
            member_rel = os.path.join("scans", "1", "resources", "NIFTI", "files", "input_1.nii.gz")
            os.makedirs(os.path.join(accession_dir, os.path.dirname(member_rel)))
            with open(os.path.join(accession_dir, member_rel), "w") as f:
                f.write("old-data")
            stale_file = os.path.join(accession_dir, "old_file.txt")
            with open(stale_file, "w") as f:
                f.write("stale")

            # Top-level folder is the accession id, NOT the zip stem "ACC123-scans-NIFTI".
            zip_path = os.path.join(tmp_dir, "ACC123-scans-NIFTI.zip")
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr(f"ACC123/{member_rel.replace(os.sep, '/')}", "new-data")

            result = unzip_file(zip_path, tmp_dir, "ACC123")

            assert result == accession_dir
            with open(os.path.join(accession_dir, member_rel)) as f:
                assert f.read() == "new-data"  # same-name member overwritten
            assert os.path.exists(stale_file)  # unrelated pre-existing file untouched
            assert not os.path.exists(os.path.join(tmp_dir, "ACC123-scans-NIFTI"))
            assert not os.path.exists(zip_path)  # zip deleted

    def test_not_found_raises_file_not_found_error(self):
        with pytest.raises(FileNotFoundError, match="ZIP file not found"):
            unzip_file("/nonexistent/path.zip", "/tmp", "test")

    def test_zip_slip_entry_raises_value_error(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            zip_path = os.path.join(tmp_dir, "malicious.zip")

            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("../evil.txt", "bad")

            with pytest.raises(XnatFetchError, match="stray members"):
                unzip_file(zip_path, tmp_dir, "ACC123")

            assert os.path.exists(zip_path)

    def test_zip_slip_traversal_path_is_never_written_to_disk(self):
        """A traversal entry is validated and rejected before extraction;
        the malicious path is never written to disk even if safe entries
        preceding it were already extracted."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            zip_path = os.path.join(tmp_dir, "mixed.zip")

            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("safe.txt", "hello")
                zf.writestr("../evil.txt", "bad")

            with pytest.raises(XnatFetchError, match="stray members"):
                unzip_file(zip_path, tmp_dir, "ACC123")

            # The traversal path must NOT have been written to disk
            escaped_path = os.path.join(os.path.dirname(tmp_dir), "evil.txt")
            assert not os.path.exists(escaped_path)
            # The zip file is not cleaned up when extraction fails mid-way
            assert os.path.exists(zip_path)

    def test_zip_slip_traversal_only_raises_on_final_entry(self):
        """A ZIP where every entry is a traversal path must also be rejected."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            zip_path = os.path.join(tmp_dir, "all-bad.zip")

            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("../../etc/passwd", "root:x:0:0:")
                zf.writestr("../../tmp/evil.sh", "rm -rf /")

            with pytest.raises(XnatFetchError, match="stray members"):
                unzip_file(zip_path, tmp_dir, "ACC123")

            assert os.path.exists(zip_path)


# ── download_and_unzip_images ──


def _fake_unzip(zip_path, extract_dir, expected_root):
    """Stands in for unzip_file: creates and returns the accession dir like the real one."""
    accession_dir = os.path.join(extract_dir, expected_root)
    os.makedirs(accession_dir, exist_ok=True)
    return accession_dir


class TestDownloadAndUnzipImages:
    @pytest.mark.asyncio
    @patch("imaging_api.services.download.unzip_file", side_effect=_fake_unzip)
    @patch("imaging_api.services.download.download_file", side_effect=lambda url, dest, headers: dest)
    @patch("imaging_api.services.download.get_subject_id_from_experiment_response", return_value="SUBJ1")
    @patch(
        "imaging_api.services.download.get_experiment",
        return_value=EXPERIMENT_RESPONSE,
    )
    @patch("imaging_api.services.download.get_project_from_central_hub_project_id")
    @patch("imaging_api.services.download.os.walk", return_value=[])
    async def test_success(
        self, mock_walk, mock_get_project, mock_get_exp, mock_get_subj, mock_download, mock_unzip, headers, tmp_path
    ):
        mock_get_project.return_value = MagicMock(ID="PROJ1")

        with patch("imaging_api.services.download.BASE_IMAGES_DOWNLOAD_DIR", str(tmp_path)):
            result = await download_and_unzip_images(
                central_hub_project_id="hub-proj-1",
                accession_id="ACC123",
                net_id="net1",
                assessor_type="scan",
                resource_type="NIFTI",
                headers=headers,
            )

        assert result == str(tmp_path / "net1" / "hub-proj-1" / "ACC123")
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
    @patch("imaging_api.services.download.get_experiment", return_value=EXPERIMENT_RESPONSE)
    @patch("imaging_api.services.download.get_project_from_central_hub_project_id")
    async def test_download_failure(self, mock_get_project, mock_get_exp, mock_get_subj, mock_download, headers):
        mock_get_project.return_value = MagicMock(ID="PROJ1")
        mock_download.side_effect = Exception("disk full")

        with pytest.raises(Exception, match="Failed to download file"):
            await download_and_unzip_images("hub-proj-1", "ACC1", "net1", "scan", "NIFTI", headers)

    @pytest.mark.asyncio
    @patch("imaging_api.services.download.download_file")
    @patch("imaging_api.services.download.get_subject_id_from_experiment_response", return_value="SUBJ1")
    @patch("imaging_api.services.download.get_experiment", return_value=EXPERIMENT_RESPONSE)
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
    @patch("imaging_api.services.download.get_experiment", return_value=EXPERIMENT_RESPONSE)
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
    @patch("imaging_api.services.download.get_experiment", return_value=EXPERIMENT_RESPONSE)
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
                await download_and_unzip_images("hub-proj-1", "../../etc/passwd", "net1", "scan", "NIFTI", headers)

    @pytest.mark.asyncio
    @patch("imaging_api.services.download.download_file")
    @patch("imaging_api.services.download.get_subject_id_from_experiment_response", return_value="SUBJ1")
    @patch("imaging_api.services.download.get_experiment", return_value=EXPERIMENT_RESPONSE)
    @patch("imaging_api.services.download.get_project_from_central_hub_project_id")
    @patch("imaging_api.services.download.unzip_file", side_effect=_fake_unzip)
    async def test_net_id_directory_is_created_on_demand(
        self, mock_unzip, mock_get_project, mock_get_exp, mock_get_subj, mock_download, headers
    ):
        """First-use of a net_id in a trust without the subdir pre-provisioned
        must work — imaging-api creates it rather than 404-ing."""
        mock_get_project.return_value = MagicMock(ID="PROJ1")
        with tempfile.TemporaryDirectory() as tmp:
            expected_net_dir = os.path.join(tmp, "net-never-seen-before")
            mock_download.return_value = os.path.join(expected_net_dir, "ACC1-scans-NIFTI.zip")

            with patch("imaging_api.services.download.BASE_IMAGES_DOWNLOAD_DIR", tmp):
                await download_and_unzip_images("hub-proj-1", "ACC1", "net-never-seen-before", "scan", "NIFTI", headers)

            assert os.path.isdir(expected_net_dir), f"download_and_unzip_images should have created {expected_net_dir}"

    @pytest.mark.asyncio
    @patch("imaging_api.services.download.os.makedirs", side_effect=PermissionError("read-only fs"))
    async def test_net_dir_creation_failure_raises_local_storage_error(self, mock_makedirs, headers):
        """If the trust host can't create the net_id subdir at all (read-only
        FS, perms on the bind-mount root, etc.), surface it as
        LocalStorageError with a pointer to the offending path — not as a 404
        with an XNAT URL."""
        with pytest.raises(LocalStorageError, match="Cannot create image download directory"):
            await download_and_unzip_images("hub-proj-1", "ACC1", "some-net", "scan", "NIFTI", headers)


# ── download cache (FLIP#953) ──


def _seed_cached_download(base, net_id, project, accession, assessor="scan", resource="NIFTI", stats=STATS):
    """Materialises a completed cached download: one file in the resource's folder + its sentinel."""
    accession_dir = os.path.realpath(os.path.join(base, net_id, project, accession))
    image = Path(accession_dir, f"{assessor}s", "1-x", "resources", resource, "files", "image")
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_text("cached")
    write_sentinel(accession_dir, assessor, resource, CachedDownload("XNAT_E00001", stats))
    return accession_dir


class TestDownloadCache:
    """A completed (net, project, accession, assessor, resource) download is served from local
    disk after one XNAT stats check; the completeness sentinel — not file presence — is the key,
    because extraction merges member-by-member and a crash can leave a partial folder."""

    @pytest.mark.asyncio
    @patch("imaging_api.services.download.unzip_file")
    @patch("imaging_api.services.download.download_file")
    @patch("imaging_api.services.download.get_subject_id_from_experiment_response")
    @patch("imaging_api.services.download.get_experiment")
    @patch("imaging_api.services.download.get_project_from_central_hub_project_id")
    async def test_cache_hit_makes_one_stats_call_and_no_download(
        self,
        mock_get_project,
        mock_get_exp,
        mock_get_subj,
        mock_download,
        mock_unzip,
        headers,
        tmp_path,
        resource_stats,
    ):
        accession_dir = _seed_cached_download(str(tmp_path), "net1", "hub-proj-1", "ACC123")

        with patch("imaging_api.services.download.BASE_IMAGES_DOWNLOAD_DIR", str(tmp_path)):
            result = await download_and_unzip_images("hub-proj-1", "ACC123", "net1", "scan", "NIFTI", headers)

        assert result == accession_dir
        resource_stats.assert_called_once_with("XNAT_E00001", "NIFTI", headers)
        for xnat_mock in (mock_get_project, mock_get_exp, mock_get_subj, mock_download, mock_unzip):
            xnat_mock.assert_not_called()

    @pytest.mark.asyncio
    @patch("imaging_api.services.download.unzip_file", side_effect=_fake_unzip)
    @patch("imaging_api.services.download.download_file", side_effect=lambda url, dest, headers: dest)
    @patch("imaging_api.services.download.get_subject_id_from_experiment_response", return_value="SUBJ1")
    @patch("imaging_api.services.download.get_experiment", return_value=EXPERIMENT_RESPONSE)
    @patch("imaging_api.services.download.get_project_from_central_hub_project_id")
    async def test_cache_miss_downloads_then_writes_sentinel(
        self, mock_get_project, mock_get_exp, mock_get_subj, mock_download, mock_unzip, headers, tmp_path
    ):
        mock_get_project.return_value = MagicMock(ID="PROJ1")

        with patch("imaging_api.services.download.BASE_IMAGES_DOWNLOAD_DIR", str(tmp_path)):
            result = await download_and_unzip_images("hub-proj-1", "ACC123", "net1", "scan", "NIFTI", headers)

        project_dir = os.path.realpath(os.path.join(str(tmp_path), "net1", "hub-proj-1"))
        assert result == os.path.join(project_dir, "ACC123")
        assert os.path.isfile(os.path.join(result, ".flip_complete-scan-NIFTI"))
        # Extraction must be rooted in the per-project dir so the accession folder lands inside it.
        _, extract_dir, new_name = mock_unzip.call_args[0]
        assert extract_dir == project_dir
        assert new_name == "ACC123"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("requested_resource", ["DICOM", "ALL"])
    @patch("imaging_api.services.download.unzip_file", side_effect=_fake_unzip)
    @patch("imaging_api.services.download.download_file", side_effect=lambda url, dest, headers: dest)
    @patch("imaging_api.services.download.get_subject_id_from_experiment_response", return_value="SUBJ1")
    @patch("imaging_api.services.download.get_experiment", return_value=EXPERIMENT_RESPONSE)
    @patch("imaging_api.services.download.get_project_from_central_hub_project_id")
    async def test_sentinel_is_exact_match_per_resource_type(
        self,
        mock_get_project,
        mock_get_exp,
        mock_get_subj,
        mock_download,
        mock_unzip,
        requested_resource,
        headers,
        tmp_path,
    ):
        """A cached NIFTI download must not satisfy a DICOM request, nor an ALL request —
        sentinels match exactly per (assessor, resource)."""
        mock_get_project.return_value = MagicMock(ID="PROJ1")
        _seed_cached_download(str(tmp_path), "net1", "hub-proj-1", "ACC123", resource="NIFTI")

        with patch("imaging_api.services.download.BASE_IMAGES_DOWNLOAD_DIR", str(tmp_path)):
            await download_and_unzip_images("hub-proj-1", "ACC123", "net1", "scan", requested_resource, headers)

        mock_download.assert_called_once()

    @pytest.mark.asyncio
    @patch("imaging_api.services.download.unzip_file", side_effect=_fake_unzip)
    @patch("imaging_api.services.download.download_file", side_effect=lambda url, dest, headers: dest)
    @patch("imaging_api.services.download.get_subject_id_from_experiment_response", return_value="SUBJ1")
    @patch("imaging_api.services.download.get_experiment", return_value=EXPERIMENT_RESPONSE)
    @patch("imaging_api.services.download.get_project_from_central_hub_project_id")
    async def test_sentinel_is_per_project(
        self, mock_get_project, mock_get_exp, mock_get_subj, mock_download, mock_unzip, headers, tmp_path
    ):
        """Two projects sharing an accession must not be served each other's copies — their
        XNAT content can differ (e.g. per-project label enrichment)."""
        mock_get_project.return_value = MagicMock(ID="PROJ1")
        _seed_cached_download(str(tmp_path), "net1", "hub-proj-A", "ACC123")

        with patch("imaging_api.services.download.BASE_IMAGES_DOWNLOAD_DIR", str(tmp_path)):
            result = await download_and_unzip_images("hub-proj-B", "ACC123", "net1", "scan", "NIFTI", headers)

        mock_download.assert_called_once()
        assert result == os.path.realpath(os.path.join(str(tmp_path), "net1", "hub-proj-B", "ACC123"))

    @pytest.mark.asyncio
    @patch("imaging_api.services.download.unzip_file", side_effect=_fake_unzip)
    @patch("imaging_api.services.download.get_subject_id_from_experiment_response", return_value="SUBJ1")
    @patch("imaging_api.services.download.get_experiment", return_value=EXPERIMENT_RESPONSE)
    @patch("imaging_api.services.download.get_project_from_central_hub_project_id")
    async def test_force_refresh_invalidates_before_redownload(
        self, mock_get_project, mock_get_exp, mock_get_subj, mock_unzip, headers, tmp_path
    ):
        """force_refresh must remove the sentinel BEFORE the re-download starts (a crash
        mid-refresh must not leave a stale completeness marker) and rewrite it on success."""
        mock_get_project.return_value = MagicMock(ID="PROJ1")
        accession_dir = _seed_cached_download(str(tmp_path), "net1", "hub-proj-1", "ACC123")
        sentinel = os.path.join(accession_dir, ".flip_complete-scan-NIFTI")

        def _download_asserting_sentinel_gone(url, dest, headers):
            assert not os.path.exists(sentinel), "sentinel must be gone before the re-download starts"
            return dest

        with (
            patch("imaging_api.services.download.BASE_IMAGES_DOWNLOAD_DIR", str(tmp_path)),
            patch(
                "imaging_api.services.download.download_file", side_effect=_download_asserting_sentinel_gone
            ) as mock_download,
        ):
            result = await download_and_unzip_images(
                "hub-proj-1", "ACC123", "net1", "scan", "NIFTI", headers, force_refresh=True
            )

        mock_download.assert_called_once()
        assert result == accession_dir
        assert os.path.isfile(sentinel)

    @pytest.mark.asyncio
    @patch("imaging_api.services.download.get_subject_id_from_experiment_response", return_value="SUBJ1")
    @patch("imaging_api.services.download.get_experiment", return_value=EXPERIMENT_RESPONSE)
    @patch("imaging_api.services.download.get_project_from_central_hub_project_id")
    async def test_real_default_layout_zip_is_extracted_and_marked_complete(
        self, mock_get_project, mock_get_exp, mock_get_subj, headers, tmp_path
    ):
        mock_get_project.return_value = MagicMock(ID="PROJ1")

        def _xnat_default_zip(url, dest, headers):
            return _write_zip(dest, [XNAT_DEFAULT_MEMBER])

        with (
            patch("imaging_api.services.download.BASE_IMAGES_DOWNLOAD_DIR", str(tmp_path)),
            patch("imaging_api.services.download.download_file", side_effect=_xnat_default_zip),
        ):
            result = await download_and_unzip_images("hub-proj-1", "ACC123", "net1", "scan", "NIFTI", headers)

        project_dir = tmp_path / "net1" / "hub-proj-1"
        assert result == str(project_dir / "ACC123")
        assert (project_dir / XNAT_DEFAULT_MEMBER).is_file()
        assert (project_dir / "ACC123" / ".flip_complete-scan-NIFTI").is_file()
        assert sorted(os.listdir(project_dir)) == ["ACC123"]  # zip removed, nothing beside the study

    @pytest.mark.asyncio
    @patch("imaging_api.services.download.get_subject_id_from_experiment_response", return_value="SUBJ1")
    @patch("imaging_api.services.download.get_experiment", return_value=EXPERIMENT_RESPONSE)
    @patch("imaging_api.services.download.get_project_from_central_hub_project_id")
    async def test_mislaid_zip_is_refused_and_never_cached(
        self, mock_get_project, mock_get_exp, mock_get_subj, headers, tmp_path
    ):
        mock_get_project.return_value = MagicMock(ID="PROJ1")

        def _improved_zip(url, dest, headers):
            return _write_zip(dest, ["scans/173680_unknown/NIFTI/input.nii.gz"])

        with (
            patch("imaging_api.services.download.BASE_IMAGES_DOWNLOAD_DIR", str(tmp_path)),
            patch("imaging_api.services.download.download_file", side_effect=_improved_zip),
        ):
            with pytest.raises(XnatFetchError):
                await download_and_unzip_images("hub-proj-1", "ACC123", "net1", "scan", "NIFTI", headers)

        project_dir = tmp_path / "net1" / "hub-proj-1"
        assert not (project_dir / "ACC123" / ".flip_complete-scan-NIFTI").exists()
        assert not (project_dir / "scans").exists()

    @pytest.mark.asyncio
    @patch("imaging_api.services.download.download_file", side_effect=Exception("connection reset"))
    @patch("imaging_api.services.download.get_subject_id_from_experiment_response", return_value="SUBJ1")
    @patch("imaging_api.services.download.get_experiment", return_value=EXPERIMENT_RESPONSE)
    @patch("imaging_api.services.download.get_project_from_central_hub_project_id")
    async def test_failed_download_leaves_no_sentinel(
        self, mock_get_project, mock_get_exp, mock_get_subj, mock_download, headers, tmp_path
    ):
        mock_get_project.return_value = MagicMock(ID="PROJ1")

        with patch("imaging_api.services.download.BASE_IMAGES_DOWNLOAD_DIR", str(tmp_path)):
            with pytest.raises(Exception, match="Failed to download file"):
                await download_and_unzip_images("hub-proj-1", "ACC123", "net1", "scan", "NIFTI", headers)

        sentinel = os.path.join(str(tmp_path), "net1", "hub-proj-1", "ACC123", ".flip_complete-scan-NIFTI")
        assert not os.path.exists(sentinel)

    @pytest.mark.asyncio
    @patch("imaging_api.services.download.get_project_from_central_hub_project_id")
    async def test_project_id_path_traversal_is_rejected(self, mock_get_project, headers, tmp_path):
        """The project cache segment is user-controlled; a traversal value must be rejected
        before any XNAT call."""
        with patch("imaging_api.services.download.BASE_IMAGES_DOWNLOAD_DIR", str(tmp_path)):
            with pytest.raises(ValueError, match="Path traversal detected in central hub project ID"):
                await download_and_unzip_images("../../escape", "ACC1", "net1", "scan", "NIFTI", headers)

        mock_get_project.assert_not_called()


# ── a hit is checked against XNAT (FLIP#1456 F1) ──

ENRICHED = [["173680", "NIFTI", "82", "3", "400113"]]  # STATS after two label files were uploaded
DICOM_STATS = [["173680", "DICOM", "3", "1", "263410"]]


@pytest.fixture
def xnat(tmp_path):
    """The miss path against a stand-in XNAT: the download writes a default-layout zip of ``xnat.members``."""
    state = MagicMock(members=[XNAT_DEFAULT_MEMBER])
    with (
        patch("imaging_api.services.download.BASE_IMAGES_DOWNLOAD_DIR", str(tmp_path)),
        patch("imaging_api.services.download.get_project_from_central_hub_project_id", return_value=MagicMock(ID="P")),
        patch("imaging_api.services.download.get_experiment", return_value=EXPERIMENT_RESPONSE) as state.get_experiment,
        patch(
            "imaging_api.services.download.download_file",
            side_effect=lambda url, dest, h: _write_zip(dest, state.members),
        ) as state.download,
    ):
        yield state


def _fetch(headers, net="net1", assessor="scan", resource="NIFTI", **kwargs):
    return asyncio.run(download_and_unzip_images("hub-proj-1", "ACC123", net, assessor, resource, headers, **kwargs))


def _file(accession_dir, resource):
    return Path(accession_dir, "scans", "1-x", "resources", resource, "files", "image")


class TestHitCheckedAgainstXnat:
    def test_a_change_replaces_only_that_resource(self, headers, tmp_path, xnat, resource_stats):
        """flip.get_by_accession_number([DICOM, NIFTI]) fetches each type into one folder: a stale NIFTI
        must not take the DICOM copy with it."""
        acc = _seed_cached_download(str(tmp_path), "net1", "hub-proj-1", "ACC123", resource="DICOM", stats=DICOM_STATS)
        _seed_cached_download(str(tmp_path), "net1", "hub-proj-1", "ACC123", resource="ALL")
        _seed_cached_download(str(tmp_path), "net1", "hub-proj-1", "ACC123")
        resource_stats.side_effect = lambda e, r, h: DICOM_STATS if r == "DICOM" else ENRICHED

        assert _fetch(headers, resource="DICOM") == acc
        assert _fetch(headers, resource="NIFTI") == acc

        xnat.download.assert_called_once()
        assert not _file(acc, "NIFTI").exists()  # deleted, not merged into
        assert (tmp_path / "net1" / "hub-proj-1" / XNAT_DEFAULT_MEMBER).is_file()
        assert read_sentinel(acc, "scan", "NIFTI").resource_stats == ENRICHED
        assert read_sentinel(acc, "scan", "ALL") is None  # covered the NIFTI files
        assert _file(acc, "DICOM").is_file()
        assert read_sentinel(acc, "scan", "DICOM").resource_stats == DICOM_STATS

        _fetch(headers, resource="DICOM")
        _fetch(headers, resource="NIFTI")
        xnat.download.assert_called_once()  # both hits now

    def test_other_nets_and_projects_are_left_alone(self, headers, tmp_path, xnat, resource_stats):
        _seed_cached_download(str(tmp_path), "net1", "hub-proj-1", "ACC123")
        others = [
            _seed_cached_download(str(tmp_path), *where, "ACC123") for where in (("net2", "hub-proj-1"), ("net1", "p2"))
        ]
        resource_stats.return_value = ENRICHED

        _fetch(headers)

        for acc in others:
            assert _file(acc, "NIFTI").is_file()
            assert read_sentinel(acc, "scan", "NIFTI").resource_stats == STATS

    def test_a_deleted_experiment_is_looked_up_again_by_label(self, headers, tmp_path, xnat, resource_stats):
        acc = _seed_cached_download(str(tmp_path), "net1", "hub-proj-1", "ACC123")
        resource_stats.side_effect = [NotFoundError("gone"), STATS]

        assert _fetch(headers) == acc

        xnat.get_experiment.assert_called_once_with(project_id="P", experiment_id_or_label="ACC123", headers=headers)
        xnat.download.assert_called_once()

    def test_a_study_gone_from_xnat_is_gone_from_the_cache(self, headers, tmp_path, xnat, resource_stats):
        acc = _seed_cached_download(str(tmp_path), "net1", "hub-proj-1", "ACC123")
        resource_stats.side_effect = NotFoundError("gone")
        xnat.get_experiment.side_effect = NotFoundError("gone")

        with pytest.raises(NotFoundError):
            _fetch(headers)

        assert not _file(acc, "NIFTI").exists()
        assert read_sentinel(acc, "scan", "NIFTI") is None

    @pytest.mark.parametrize("failure", [requests.ConnectionError("refused"), XnatFetchError("502")])
    def test_xnat_unable_to_answer_serves_the_copy_with_a_warning(
        self, headers, tmp_path, xnat, resource_stats, caplog, failure
    ):
        acc = _seed_cached_download(str(tmp_path), "net1", "hub-proj-1", "ACC123")
        resource_stats.side_effect = failure

        with caplog.at_level(logging.WARNING, logger="imaging_api.utils.logger"):
            assert _fetch(headers) == acc

        xnat.download.assert_not_called()
        assert _file(acc, "NIFTI").is_file()
        assert "could not check XNAT" in caplog.text

    def test_a_stale_copy_is_never_served_even_when_the_lookups_fail(self, headers, tmp_path, xnat, resource_stats):
        _seed_cached_download(str(tmp_path), "net1", "hub-proj-1", "ACC123")
        resource_stats.return_value = ENRICHED
        xnat.get_experiment.side_effect = Exception("connection refused")

        with pytest.raises(Exception, match="connection refused"):
            _fetch(headers)

    def test_an_unreadable_sentinel_is_replaced_once_xnat_answers(self, headers, tmp_path, xnat, resource_stats):
        acc = _seed_cached_download(str(tmp_path), "net1", "hub-proj-1", "ACC123")
        Path(acc, ".flip_complete-scan-NIFTI").write_text("")  # holds no record

        _fetch(headers)

        xnat.download.assert_called_once()
        assert not _file(acc, "NIFTI").exists()
        assert read_sentinel(acc, "scan", "NIFTI") == CachedDownload("XNAT_E00001", STATS)

    def test_an_unreadable_sentinel_is_never_served_unchecked(self, headers, tmp_path, xnat):
        """Without a record there is nothing to check the copy against, so it is a miss, not a hit."""
        acc = _seed_cached_download(str(tmp_path), "net1", "hub-proj-1", "ACC123")
        Path(acc, ".flip_complete-scan-NIFTI").write_text("")
        xnat.get_experiment.side_effect = Exception("connection refused")

        with pytest.raises(Exception, match="connection refused"):
            _fetch(headers)

        xnat.download.assert_not_called()

    def test_a_malformed_record_is_refreshed_not_a_500(self, headers, tmp_path, xnat, resource_stats):
        acc = _seed_cached_download(str(tmp_path), "net1", "hub-proj-1", "ACC123", stats=[["1"]])

        _fetch(headers)

        assert read_sentinel(acc, "scan", "NIFTI").resource_stats == STATS

    def test_an_assessor_download_is_not_checked(self, headers, tmp_path, xnat, resource_stats):
        """XNAT 1.10 answers 404 for assessors/ALL/resources, so an assessor download records no stats."""
        _fetch(headers, assessor="assessor", resource="SEG")
        _fetch(headers, assessor="assessor", resource="SEG")

        resource_stats.assert_not_called()
        xnat.download.assert_called_once()

    def test_the_miss_reads_the_stats_before_the_download(self, headers, tmp_path, xnat, resource_stats):
        """Read after the zip, a change landing in between would be marked current for good."""
        calls = []
        resource_stats.side_effect = lambda *a: calls.append("stats") or STATS
        download_zip = xnat.download.side_effect
        xnat.download.side_effect = lambda *a: calls.append("download") or download_zip(*a)

        _fetch(headers)

        assert calls == ["stats", "download"]

    def test_a_failed_refresh_leaves_no_sentinel(self, headers, tmp_path, xnat, resource_stats):
        acc = _seed_cached_download(str(tmp_path), "net1", "hub-proj-1", "ACC123")
        resource_stats.return_value = ENRICHED
        xnat.download.side_effect = Exception("connection reset")

        with pytest.raises(Exception, match="Failed to download file"):
            _fetch(headers)

        assert read_sentinel(acc, "scan", "NIFTI") is None

    def test_a_copy_that_cannot_be_deleted_is_a_storage_error(self, headers, tmp_path, xnat, resource_stats):
        _seed_cached_download(str(tmp_path), "net1", "hub-proj-1", "ACC123")
        resource_stats.return_value = ENRICHED

        with (
            patch("imaging_api.services.download.clear_resource", side_effect=PermissionError("denied")),
            pytest.raises(LocalStorageError, match="Cannot delete the stale cached copy"),
        ):
            _fetch(headers)

        xnat.download.assert_not_called()

    def test_force_refresh_clears_then_downloads(self, headers, tmp_path, xnat, resource_stats):
        acc = _seed_cached_download(str(tmp_path), "net1", "hub-proj-1", "ACC123")

        _fetch(headers, force_refresh=True)

        resource_stats.assert_called_once()  # the miss path's; no hit check
        xnat.download.assert_called_once()
        assert not _file(acc, "NIFTI").exists()


RESOURCE_ROWS = [
    {"cat_id": "1", "label": "NIFTI", "xnat_abstractresource_id": "82", "file_count": "1", "file_size": "18"},
    {"cat_id": "1", "label": "DICOM", "xnat_abstractresource_id": "3", "file_count": "1", "file_size": "26"},
    {"cat_id": "2", "label": "secondary", "xnat_abstractresource_id": 7, "file_count": None, "file_size": 5},
]


def _listing(status_code=200, body=None):
    response = MagicMock(status_code=status_code, text="body")
    response.json.return_value = {"ResultSet": {"Result": RESOURCE_ROWS}} if body is None else body
    return response


class TestGetResourceStats:
    @patch("imaging_api.services.download.requests.get")
    def test_asks_for_the_experiments_scan_resources_with_a_short_timeout(self, mock_get):
        mock_get.return_value = _listing()

        get_resource_stats("XNAT_E00004", "NIFTI", {"Cookie": "c"})

        (url,), kwargs = mock_get.call_args
        assert urlsplit(url).path == "/data/experiments/XNAT_E00004/scans/ALL/resources"
        assert parse_qs(urlsplit(url).query) == {"format": ["json"]}
        assert kwargs == {"headers": {"Cookie": "c"}, "timeout": 5}

    @pytest.mark.parametrize(
        ("resource_type", "expected"),
        [
            ("NIFTI", [["1", "NIFTI", "82", "1", "18"]]),
            ("nifti", []),  # XNAT's label lookup is exact
            (
                "ALL",
                [["1", "DICOM", "3", "1", "26"], ["1", "NIFTI", "82", "1", "18"], ["2", "secondary", "7", "None", "5"]],
            ),
        ],
    )
    @patch("imaging_api.services.download.requests.get")
    def test_keeps_the_rows_the_download_covers(self, mock_get, resource_type, expected):
        mock_get.return_value = _listing()

        assert get_resource_stats("XNAT_E00004", resource_type, {}) == expected

    @pytest.mark.parametrize(
        ("response", "error"),
        [
            (_listing(404), NotFoundError),
            (_listing(500), XnatFetchError),
            (_listing(body={"items": []}), XnatFetchError),
        ],
    )
    @patch("imaging_api.services.download.requests.get")
    def test_failures(self, mock_get, response, error):
        mock_get.return_value = response

        with pytest.raises(error):
            get_resource_stats("XNAT_E00004", "NIFTI", {})


def test_resource_stats_experiment_id_stays_in_one_segment(xnat_path_segment, sent_xnat_requests):
    raw, encoded = xnat_path_segment
    with pytest.raises(XnatFetchError):  # the stub answers `{}`, which is not a listing
        get_resource_stats(raw, "NIFTI", {})

    (request,) = sent_xnat_requests
    assert urlsplit(request.url).path == f"/data/experiments/{encoded}/scans/ALL/resources"


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


def test_identifiers_stay_in_one_segment_at_http_boundary(xnat_path_segment, tmp_path, sent_xnat_requests):
    raw, encoded = xnat_path_segment
    url = format_download_url(raw, raw, raw, resource_type=raw)
    download_file(url, str(tmp_path / "images.zip"), {"X-Request-ID": "trace-1"})

    assert len(sent_xnat_requests) == 1
    request = sent_xnat_requests[0]
    parsed = urlsplit(request.url)
    assert parsed.path == (
        f"/data/projects/{encoded}/subjects/{encoded}/experiments/{encoded}/scans/ALL/resources/{encoded}/files"
    )
    assert parsed.query == "format=zip&structure=default"
    assert parsed.fragment == ""
    assert request.headers["X-Request-ID"] == "trace-1"


@pytest.mark.parametrize("invalid", ["", ".", ".."])
@pytest.mark.parametrize("field", ["project_id", "subject_id", "experiment_id_or_label", "resource_type"])
def test_empty_and_dot_segments_fail_before_http(field, invalid, sent_xnat_requests):
    kwargs = {"project_id": "PROJ", "subject_id": "SUBJ", "experiment_id_or_label": "EXP", field: invalid}
    with pytest.raises(ValueError, match="empty or a dot-segment"):
        format_download_url(**kwargs)
    assert sent_xnat_requests == []
