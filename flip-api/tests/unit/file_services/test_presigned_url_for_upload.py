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

import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from flip_api.file_services.presigned_url_for_upload import (
    _normalised_extension,
    _validate_upload_request,
)


@pytest.fixture(autouse=True)
def fake_settings():
    """Patch get_settings inside the module to a small in-memory object so the
    test does not depend on the host's .env.development file or AWS access."""
    fake = MagicMock()
    fake.ALLOWED_MODEL_FILE_EXTENSIONS = ["py", "json", "pt"]
    fake.MAX_MODEL_FILE_SIZE_BYTES = 1_000
    fake.PRE_SIGNED_URL = None
    fake.UPLOADED_MODEL_FILES_BUCKET = "s3://staging"
    with patch("flip_api.file_services.presigned_url_for_upload.get_settings", return_value=fake):
        yield fake


@pytest.mark.parametrize(
    ("name", "expected"),
    [("a.PT", "pt"), ("a.tar.gz", "gz"), ("noext", ""), ("a.JSON", "json")],
)
def test_normalised_extension(name, expected):
    assert _normalised_extension(name) == expected


def test_validate_accepts_whitelisted_extension():
    _validate_upload_request("model.pt", file_size=100)


@pytest.mark.parametrize("bad_name", ["model.exe", "model.sh", "noext", "weights.bin"])
def test_validate_rejects_disallowed_extension(bad_name):
    with pytest.raises(HTTPException) as exc:
        _validate_upload_request(bad_name, file_size=None)
    assert exc.value.status_code == 400


@pytest.mark.parametrize("bad_name", ["", "a/b.py", "a\\b.py", "a\x00b.py"])
def test_validate_rejects_path_traversal_or_empty(bad_name):
    with pytest.raises(HTTPException) as exc:
        _validate_upload_request(bad_name, file_size=None)
    assert exc.value.status_code == 400


def test_validate_rejects_oversized_declared_size():
    with pytest.raises(HTTPException) as exc:
        _validate_upload_request("model.pt", file_size=10_000)
    assert exc.value.status_code == 400


def test_validate_allows_unset_size():
    _validate_upload_request("model.pt", file_size=None)


def test_record_pending_upload_inserts_when_missing():
    from flip_api.domain.schemas.status import FileUploadStatus
    from flip_api.file_services.presigned_url_for_upload import _record_pending_upload

    db = MagicMock()
    db.exec.return_value.first.return_value = None
    model_id = uuid.uuid4()

    _record_pending_upload(db, model_id, "f.json", file_size=512)

    db.add.assert_called_once()
    added = db.add.call_args.args[0]
    assert added.status == FileUploadStatus.SCANNING
    assert added.size == 512.0
    db.commit.assert_called_once()


def test_record_pending_upload_resets_existing_row_to_scanning():
    from flip_api.db.models.main_models import UploadedFiles
    from flip_api.domain.schemas.status import FileUploadStatus
    from flip_api.file_services.presigned_url_for_upload import _record_pending_upload

    model_id = uuid.uuid4()
    existing = UploadedFiles(
        name="f.json", status=FileUploadStatus.ERROR, size=0, type="x", model_id=model_id
    )
    db = MagicMock()
    db.exec.return_value.first.return_value = existing

    _record_pending_upload(db, model_id, "f.json", file_size=999)

    assert existing.status == FileUploadStatus.SCANNING
    assert existing.size == 999.0
    db.add.assert_not_called()
    db.commit.assert_called_once()
