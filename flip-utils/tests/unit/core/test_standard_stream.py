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
