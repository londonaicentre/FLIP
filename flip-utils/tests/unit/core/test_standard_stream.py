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

"""IMAGES_TRANSPORT=stream (FLIP#1049): studies arrive as a tar and results leave as multipart, no shared mount."""

import io
import tarfile
from unittest.mock import Mock, patch

import pytest

from flip.constants.flip_constants import ResourceType
from flip.core import standard
from flip.core.standard import FLIPStandardProd


class _Raw(io.BytesIO):
    """requests' raw stream, which the client sets decode_content on."""


def _tar(name: str, data: bytes) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        info = tarfile.TarInfo(name)
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


@pytest.fixture
def stream_constants():
    with patch("flip.core.standard.FlipConstants") as constants:
        constants.IMAGES_TRANSPORT = "stream"
        constants.IMAGING_API_URL = "https://imaging.example.com"
        constants.NET_ID = "net-1"
        constants.TRUST_INTERNAL_SERVICE_KEY_HEADER = "x-trust-internal-service-key"  # pragma: allowlist secret
        constants.TRUST_INTERNAL_SERVICE_KEY = "test-trust-internal-key"  # pragma: allowlist secret
        yield constants


def test_get_by_accession_number_streams_the_study_into_the_process_dir(stream_constants, tmp_path, monkeypatch):
    monkeypatch.setattr(standard, "_study_dir_path", tmp_path)
    response = Mock(status_code=200, raw=_Raw(_tar("ACC001/scans/1/resources/NIFTI/files/a.nii.gz", b"nifti")))

    with patch("flip.core.standard.requests.post", return_value=response) as post:
        result = FLIPStandardProd().get_by_accession_number("proj-1", "ACC001", ResourceType.NIFTI)

    assert result == tmp_path / "ACC001"
    assert (result / "scans/1/resources/NIFTI/files/a.nii.gz").read_bytes() == b"nifti"
    assert post.call_args.args[0] == "https://imaging.example.com/download/images/net-1/stream"
    assert post.call_args.kwargs["stream"] is True
    assert post.call_args.kwargs["params"] == {"assessor_type": "scan", "resource_type": "NIFTI"}
    assert post.call_args.kwargs["headers"]["x-trust-internal-service-key"] == "test-trust-internal-key"


def test_the_study_dir_is_made_once_and_removed_at_exit(monkeypatch):
    monkeypatch.setattr(standard, "_study_dir_path", None)
    registered = []
    monkeypatch.setattr(standard.atexit, "register", lambda func, *args, **kwargs: registered.append((func, args)))

    first = standard._study_dir()
    try:
        assert first.is_dir()
        assert first.name.startswith("flip-studies-")
        assert standard._study_dir() == first
        assert registered == [(standard.shutil.rmtree, (first,))]
    finally:
        standard.shutil.rmtree(first, ignore_errors=True)


def test_add_resource_sends_the_files_themselves(stream_constants, tmp_path):
    segmentation = tmp_path / "seg.nii.gz"
    segmentation.write_bytes(b"seg")

    with patch("flip.core.standard.requests.put", return_value=Mock(status_code=200)) as put:
        FLIPStandardProd().add_resource("proj-1", "ACC001", "scan-1", "SEG", [str(segmentation)])

    assert put.call_args.args[0] == "https://imaging.example.com/upload/images/net-1/files"
    assert put.call_args.kwargs["data"] == {
        "encrypted_central_hub_project_id": "proj-1",
        "accession_id": "ACC001",
        "scan_id": "scan-1",
        "resource_id": "SEG",
    }
    [(field, (name, handle))] = put.call_args.kwargs["files"]
    assert (field, name) == ("files", "seg.nii.gz")
    assert handle.closed  # the ExitStack closed it after the request


@pytest.fixture
def project_constants(stream_constants):
    """Only the project's own images folder is mounted (an NVFLARE study mount, FLIP#1678)."""
    stream_constants.IMAGES_TRANSPORT = "project"
    return stream_constants


def test_a_mounted_project_folder_is_read_by_path(project_constants):
    response = Mock(status_code=200, text="ok", json=lambda: {"path": "/app/data/images/net-1/proj-1/ACC001"})

    with patch("flip.core.standard.requests.post", return_value=response) as post:
        result = FLIPStandardProd().get_by_accession_number("proj-1", "ACC001", ResourceType.NIFTI)

    assert str(result) == "/app/data/images/net-1/proj-1/ACC001"
    assert post.call_args.args[0] == "https://imaging.example.com/download/images/net-1"


def test_a_mounted_project_folder_still_uploads_the_files_themselves(project_constants, tmp_path):
    segmentation = tmp_path / "seg.nii.gz"
    segmentation.write_bytes(b"seg")

    with patch("flip.core.standard.requests.put", return_value=Mock(status_code=200)) as put:
        FLIPStandardProd().add_resource("proj-1", "ACC001", "scan-1", "SEG", [str(segmentation)])

    assert put.call_args.args[0] == "https://imaging.example.com/upload/images/net-1/files"


@pytest.fixture
def archive_constants(stream_constants, tmp_path, monkeypatch):
    """The project's directory of XNAT's archive is mounted read-only (an NVFLARE study mount, FLIP#1857)."""
    stream_constants.IMAGES_TRANSPORT = "archive"
    stream_constants.XNAT_ARCHIVE_DIR = str(tmp_path / "archive")
    monkeypatch.setattr(standard, "_study_dir_path", tmp_path / "studies")
    nifti = tmp_path / "archive" / "arc001" / "ACC001" / "SCANS" / "1" / "NIFTI"
    nifti.mkdir(parents=True)
    (nifti / "a.nii.gz").write_bytes(b"nifti")
    (tmp_path / "studies").mkdir()
    return stream_constants


def _layout(*files: tuple[str, str]) -> Mock:
    return Mock(status_code=200, json=lambda: {"files": [{"path": p, "archive": a} for p, a in files]})


_NIFTI = ("ACC001/scans/1-unknown/resources/NIFTI/files/a.nii.gz", "arc001/ACC001/SCANS/1/NIFTI/a.nii.gz")


def test_an_archived_study_is_laid_out_as_links_into_the_mount(archive_constants, tmp_path):
    with patch("flip.core.standard.requests.post", return_value=_layout(_NIFTI)) as post:
        result = FLIPStandardProd().get_by_accession_number("proj-1", "ACC001", ResourceType.NIFTI)

    assert result == tmp_path / "studies" / "ACC001"
    link = result / "scans/1-unknown/resources/NIFTI/files/a.nii.gz"
    assert link.is_symlink()
    assert link.resolve() == (tmp_path / "archive/arc001/ACC001/SCANS/1/NIFTI/a.nii.gz").resolve()
    assert link.read_bytes() == b"nifti"
    assert [p.name for p in result.rglob("*.nii.gz")] == ["a.nii.gz"]
    assert post.call_args.args[0] == "https://imaging.example.com/download/images/net-1/layout"
    assert post.call_args.kwargs["params"] == {"resource_type": "NIFTI"}
    assert post.call_args.kwargs["headers"]["x-trust-internal-service-key"] == "test-trust-internal-key"


def test_an_archived_study_can_be_linked_again_the_next_round(archive_constants):
    with patch("flip.core.standard.requests.post", return_value=_layout(_NIFTI)):
        FLIPStandardProd().get_by_accession_number("proj-1", "ACC001", ResourceType.NIFTI)
        result = FLIPStandardProd().get_by_accession_number("proj-1", "ACC001", ResourceType.NIFTI)

    assert (result / "scans/1-unknown/resources/NIFTI/files/a.nii.gz").read_bytes() == b"nifti"


def test_assessor_resources_of_an_archived_study_are_streamed(archive_constants, tmp_path):
    """XNAT keeps assessors outside the session's scans, and has no listing for them."""
    tar = Mock(status_code=200, raw=_Raw(_tar("ACC001/assessors/seg/resources/SEG/files/s.dcm", b"seg")))

    with patch("flip.core.standard.requests.post", side_effect=[_layout(_NIFTI), tar]) as post:
        result = FLIPStandardProd().get_by_accession_number(
            "proj-1", "ACC001", [ResourceType.NIFTI, ResourceType.SEGMENTATION]
        )

    assert [call.args[0].rsplit("/", 1)[1] for call in post.call_args_list] == ["layout", "stream"]
    assert (result / "assessors/seg/resources/SEG/files/s.dcm").read_bytes() == b"seg"
    assert (result / "scans/1-unknown/resources/NIFTI/files/a.nii.gz").is_symlink()


@pytest.mark.parametrize(
    "file",
    [
        ("../outside/a.nii.gz", _NIFTI[1]),
        ("/etc/a.nii.gz", _NIFTI[1]),
        (_NIFTI[0], "arc001/../../other-project/a.nii.gz"),
        (_NIFTI[0], "/etc/passwd"),
    ],
)
def test_a_listed_path_that_leaves_its_directory_is_refused(archive_constants, tmp_path, file):
    with (
        patch("flip.core.standard.requests.post", return_value=_layout(file)),
        pytest.raises(ValueError, match="leaves"),
    ):
        FLIPStandardProd().get_by_accession_number("proj-1", "ACC001", ResourceType.NIFTI)

    assert not list((tmp_path / "studies").rglob("*.nii.gz"))


def test_a_listed_file_missing_from_the_mount_fails_the_fetch(archive_constants):
    missing = (_NIFTI[0], "arc001/ACC001/SCANS/1/NIFTI/gone.nii.gz")

    with patch("flip.core.standard.requests.post", return_value=_layout(missing)), pytest.raises(FileNotFoundError):
        FLIPStandardProd().get_by_accession_number("proj-1", "ACC001", ResourceType.NIFTI)


def test_an_archived_study_still_uploads_the_files_themselves(archive_constants, tmp_path):
    segmentation = tmp_path / "seg.nii.gz"
    segmentation.write_bytes(b"seg")

    with patch("flip.core.standard.requests.put", return_value=Mock(status_code=200)) as put:
        FLIPStandardProd().add_resource("proj-1", "ACC001", "scan-1", "SEG", [str(segmentation)])

    assert put.call_args.args[0] == "https://imaging.example.com/upload/images/net-1/files"
