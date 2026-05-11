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

"""Unit tests for ``flip_api.domain.schemas.file``.

The ``UploadFileBody.fileName`` validator is the server-side last line
of defence: that string is concatenated into the S3 object key by the
upload route, so a path-traversal token like ``../other-model/x`` or
control characters would let a caller steer the key off the
``{model_id}/`` prefix or smuggle an unexpected object past the
downstream scan/download endpoints. Pin every rejection rule.
"""

import pytest
from pydantic import ValidationError

from flip_api.domain.schemas.file import ModelFileDownload, UploadFileBody, validate_safe_file_name


@pytest.mark.parametrize(
    "bad_name",
    [
        "../escape.bin",
        "..",
        ".",
        "subdir/file.bin",
        "subdir\\file.bin",
        "file\x00.bin",
        "file\x07.bin",
        "file\x7f.bin",
        "  leading-space.bin",
        "trailing-space.bin  ",
        "",
    ],
)
def test_upload_file_body_rejects_unsafe_file_names(bad_name):
    """``body.fileName`` is concatenated into the S3 key — reject anything dangerous."""
    with pytest.raises(ValidationError):
        UploadFileBody(fileName=bad_name)


@pytest.mark.parametrize(
    "good_name",
    [
        "weights.bin",
        "model.tar.gz",
        "MyModel-v2.pth",
        "report (final).pdf",
        "spaces in name.bin",
    ],
)
def test_upload_file_body_accepts_safe_file_names(good_name):
    """Sanity check: ordinary filenames must still pass."""
    body = UploadFileBody(fileName=good_name)
    assert body.fileName == good_name


def test_upload_file_body_max_length_caps_at_255():
    """Reject pathologically long names that could bloat the S3 key."""
    with pytest.raises(ValidationError):
        UploadFileBody(fileName="a" * 256)


@pytest.mark.parametrize(
    "bad_name",
    [
        "../escape.bin",
        "subdir/file.bin",
        "subdir\\file.bin",
        "file\x00.bin",
        "file\x7f.bin",
    ],
)
def test_model_file_download_rejects_unsafe_file_names(bad_name):
    """``ModelFileDownload.fileName`` carries the same constraint as the upload body."""
    import uuid

    with pytest.raises(ValidationError):
        ModelFileDownload(fileName=bad_name, model_id=uuid.uuid4(), user_id=uuid.uuid4())


@pytest.mark.parametrize(
    "bad_name",
    [
        "../escape.bin",
        "subdir/file.bin",
        "subdir\\file.bin",
        "..",
        "file\x00.bin",
        "file\x7f.bin",
        "  leading-space.bin",
        "",
    ],
)
def test_validate_safe_file_name_rejects_unsafe_inputs(bad_name):
    """The shared validator helper rejects every documented unsafe shape."""
    with pytest.raises(ValueError, match="file name"):
        validate_safe_file_name(bad_name)
