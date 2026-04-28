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

import json
import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from flip_api.domain.schemas.status import FileUploadStatus
from flip_api.file_services.uploaded_file import (
    _GUARDDUTY_CLEAN_STATUS,
    _GUARDDUTY_INFECTED_STATUS,
    _handle_scan_result,
    _parse_eventbridge_payload,
    _parse_object_key,
    _ScanOutcome,
)


def _make_eventbridge_message(bucket: str, key: str, scan_status: str, threats: list[str] | None = None) -> str:
    return json.dumps(
        {
            "version": "0",
            "id": "evt-1",
            "detail-type": "GuardDuty Malware Protection Object Scan Result",
            "source": "aws.guardduty",
            "detail": {
                "schemaVersion": "1.0",
                "scanStatus": "COMPLETED",
                "resourceType": "S3_OBJECT",
                "s3ObjectDetails": {"bucketName": bucket, "objectKey": key},
                "scanResultDetails": {
                    "scanResultStatus": scan_status,
                    "threats": [{"name": t} for t in (threats or [])],
                },
            },
        }
    )


@pytest.fixture
def settings():
    """Minimal fake settings object the handler reads at runtime."""
    s = MagicMock()
    s.SCANNED_MODEL_FILES_BUCKET = "s3://scanned-bucket"
    s.ALLOWED_MODEL_FILE_EXTENSIONS = ["py", "json", "pt"]
    s.MAX_MODEL_FILE_SIZE_BYTES = 1_000_000
    return s


@pytest.fixture
def s3_mock():
    with patch("flip_api.file_services.uploaded_file.S3Client") as cls:
        instance = MagicMock()
        instance.head_object.return_value = {"ContentLength": 100, "ContentType": "application/json"}
        cls.return_value = instance
        yield instance


@pytest.fixture
def settings_patch(settings):
    with patch("flip_api.file_services.uploaded_file.get_settings", return_value=settings):
        yield settings


def test_parse_object_key_happy_path():
    model_id = uuid.uuid4()
    parsed_id, name = _parse_object_key(f"{model_id}/config.json")
    assert parsed_id == model_id
    assert name == "config.json"


@pytest.mark.parametrize("key", ["", "no-slash", "/leading", "trailing/", "not-a-uuid/foo.json"])
def test_parse_object_key_invalid(key):
    with pytest.raises(HTTPException) as exc:
        _parse_object_key(key)
    assert exc.value.status_code == 400


def test_parse_eventbridge_payload_clean():
    bucket = "staging"
    key = f"{uuid.uuid4()}/file.json"
    msg = _make_eventbridge_message(bucket, key, _GUARDDUTY_CLEAN_STATUS)
    outcome = _parse_eventbridge_payload(msg)
    assert outcome.bucket == bucket
    assert outcome.key == key
    assert outcome.is_clean is True


def test_parse_eventbridge_payload_threats():
    msg = _make_eventbridge_message("b", f"{uuid.uuid4()}/x.json", _GUARDDUTY_INFECTED_STATUS, ["EICAR"])
    outcome = _parse_eventbridge_payload(msg)
    assert outcome.is_clean is False
    assert outcome.threats == ["EICAR"]


def test_parse_eventbridge_payload_malformed_json():
    with pytest.raises(HTTPException) as exc:
        _parse_eventbridge_payload("{not json")
    assert exc.value.status_code == 400


def test_parse_eventbridge_payload_missing_bucket():
    msg = json.dumps({"detail": {"scanResultDetails": {"scanResultStatus": "NO_THREATS_FOUND"}}})
    with pytest.raises(HTTPException) as exc:
        _parse_eventbridge_payload(msg)
    assert exc.value.status_code == 400


def test_handle_scan_result_clean_promotes_and_marks_completed(settings_patch, s3_mock):
    model_id = uuid.uuid4()
    file_name = "config.json"
    outcome = _ScanOutcome(
        bucket="staging-bucket",
        key=f"{model_id}/{file_name}",
        is_clean=True,
        threats=[],
        raw_status=_GUARDDUTY_CLEAN_STATUS,
    )
    db = MagicMock()
    db.exec.return_value.first.return_value = None

    result = _handle_scan_result(outcome, db)

    assert result == {"status": "promoted"}
    s3_mock.copy_object.assert_called_once_with(
        f"s3://staging-bucket/{model_id}/{file_name}",
        f"s3://scanned-bucket/{model_id}/{file_name}",
    )
    s3_mock.delete_object.assert_called_once_with(f"s3://staging-bucket/{model_id}/{file_name}")
    db.add.assert_called_once()
    added = db.add.call_args.args[0]
    assert added.status == FileUploadStatus.COMPLETED


def test_handle_scan_result_infected_deletes_and_marks_infected(settings_patch, s3_mock):
    model_id = uuid.uuid4()
    file_name = "bad.pt"
    outcome = _ScanOutcome(
        bucket="staging-bucket",
        key=f"{model_id}/{file_name}",
        is_clean=False,
        threats=["EICAR"],
        raw_status=_GUARDDUTY_INFECTED_STATUS,
    )
    db = MagicMock()
    db.exec.return_value.first.return_value = None

    result = _handle_scan_result(outcome, db)

    assert result["status"] == "rejected"
    s3_mock.delete_object.assert_called_once()
    s3_mock.copy_object.assert_not_called()
    added = db.add.call_args.args[0]
    assert added.status == FileUploadStatus.INFECTED


def test_handle_scan_result_oversize_rejected(settings_patch, s3_mock):
    s3_mock.head_object.return_value = {"ContentLength": 10_000_000, "ContentType": "application/json"}
    model_id = uuid.uuid4()
    outcome = _ScanOutcome(
        bucket="staging-bucket",
        key=f"{model_id}/config.json",
        is_clean=True,
        threats=[],
        raw_status=_GUARDDUTY_CLEAN_STATUS,
    )
    db = MagicMock()
    db.exec.return_value.first.return_value = None

    result = _handle_scan_result(outcome, db)

    assert result["status"] == "rejected"
    s3_mock.copy_object.assert_not_called()
    added = db.add.call_args.args[0]
    assert added.status == FileUploadStatus.ERROR


def test_handle_scan_result_disallowed_extension_rejected(settings_patch, s3_mock):
    model_id = uuid.uuid4()
    outcome = _ScanOutcome(
        bucket="staging-bucket",
        key=f"{model_id}/payload.exe",
        is_clean=True,
        threats=[],
        raw_status=_GUARDDUTY_CLEAN_STATUS,
    )
    db = MagicMock()
    db.exec.return_value.first.return_value = None

    result = _handle_scan_result(outcome, db)

    assert result["status"] == "rejected"
    assert "extension" in result["detail"]
    s3_mock.copy_object.assert_not_called()


def test_handle_scan_result_pickle_unsafe_blocks_promotion(settings_patch, s3_mock):
    model_id = uuid.uuid4()
    outcome = _ScanOutcome(
        bucket="staging-bucket",
        key=f"{model_id}/model.pt",
        is_clean=True,
        threats=[],
        raw_status=_GUARDDUTY_CLEAN_STATUS,
    )
    s3_mock.get_object.return_value = {"Body": MagicMock(read=lambda: b"bytes")}
    db = MagicMock()
    db.exec.return_value.first.return_value = None

    from flip_api.utils.pickle_scanner import PickleScanResult

    with patch(
        "flip_api.file_services.uploaded_file.scan_pickle_bytes",
        return_value=PickleScanResult(is_safe=False, threats=("os.system",)),
    ):
        result = _handle_scan_result(outcome, db)

    assert result["status"] == "rejected"
    s3_mock.copy_object.assert_not_called()
    added = db.add.call_args.args[0]
    assert added.status == FileUploadStatus.INFECTED


def test_handle_scan_result_pickle_safe_promotes(settings_patch, s3_mock):
    model_id = uuid.uuid4()
    outcome = _ScanOutcome(
        bucket="staging-bucket",
        key=f"{model_id}/model.pt",
        is_clean=True,
        threats=[],
        raw_status=_GUARDDUTY_CLEAN_STATUS,
    )
    s3_mock.get_object.return_value = {"Body": MagicMock(read=lambda: b"bytes")}
    db = MagicMock()
    db.exec.return_value.first.return_value = None

    from flip_api.utils.pickle_scanner import PickleScanResult

    with patch(
        "flip_api.file_services.uploaded_file.scan_pickle_bytes",
        return_value=PickleScanResult(is_safe=True, threats=()),
    ):
        result = _handle_scan_result(outcome, db)

    assert result["status"] == "promoted"
    s3_mock.copy_object.assert_called_once()
