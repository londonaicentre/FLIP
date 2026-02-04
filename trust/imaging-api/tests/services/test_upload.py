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
from imaging_api.services.upload import create_xnat_resource, create_xnat_scan, upload_file_to_xnat
from imaging_api.utils.exceptions import AlreadyExistsError

XNAT_URL = get_settings().XNAT_URL


@pytest.fixture
def headers():
    return {}


@patch("imaging_api.services.upload.requests.put")
def test_create_existing_xnat_scan(mock_put, headers):
    # Mock the response to simulate a successful response
    mock_put.return_value = MagicMock(status_code=200)

    create_xnat_scan(
        project_id="test",
        subject_id="XNAT_S00002",
        experiment_id_or_label="XNAT_E00002",
        scan_id="1_2_826_0_1_3680043_8_274_1_1_8323329_1189734_1740750875_622789",
        headers=headers,
    )


@patch("imaging_api.services.upload.requests.put")
def test_create_existing_xnat_resource(mock_put, headers):
    # Mock the response to simulate a resource already existing
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
    # Mock the response to simulate a successful upload
    mock_put.return_value = MagicMock(status_code=200)

    # Mock the check_file_exists_in_xnat to return False (file does not exist)
    mock_check_file_exists_in_xnat.return_value = False

    # Create a temporary file to simulate the file to be uploaded
    with tempfile.NamedTemporaryFile(delete=False) as temp_file:
        temp_file.write(b"Hello, world!")
    temp_file.close()
    temp_file_path = temp_file.name

    # Test data
    project_id = "test"
    subject_id = "XNAT_S00002"
    experiment_id_or_label = "XNAT_E00002"
    scan_id = "TEST_SCAN"
    resource_id = "TEST_FILES"
    exist_ok = False

    uploaded_file = upload_file_to_xnat(
        project_id=project_id,
        subject_id=subject_id,
        experiment_id_or_label=experiment_id_or_label,
        scan_id=scan_id,
        resource_id=resource_id,
        file_path=temp_file_path,
        exist_ok=exist_ok,
        headers=headers,
    )
    temp_file_name = os.path.basename(temp_file_path)
    assert (
        uploaded_file == f"{XNAT_URL}/data/projects/test/subjects/{subject_id}/"
        f"experiments/{experiment_id_or_label}/scans/{scan_id}/"
        f"resources/{resource_id}/files/{temp_file_name}?inbody=true"
    )

    # Clean up the temporary file
    os.remove(temp_file_path)
