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
from unittest.mock import MagicMock, patch

import pytest

from imaging_api.routers.schemas import ImportStudyRequest
from imaging_api.services.imaging import ping_pacs, query_by_accession_number, queue_image_import_request
from imaging_api.utils.exceptions import NotFoundError


@pytest.fixture
def headers():
    return {}


# Test for ping_pacs function
@patch("imaging_api.services.imaging.requests.get")
def test_ping_pacs(mock_get):
    pacs_id = 1
    headers = {}

    # Define the mock response data
    mock_response_data = {
        "pacsId": pacs_id,
        "successful": True,
        "pingTime": 123,
        "created": 1610000000,
        "enabled": True,
        "timestamp": 1610001234,
        "id": 1,
        "disabled": 0,
    }

    # Configure the mock
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = mock_response_data

    # Assign the mock response to requests.get
    mock_get.return_value = mock_response

    # Call the function
    pacs_status = ping_pacs(pacs_id, headers)

    # Assertions
    assert pacs_status.successful is True
    assert pacs_status.enabled is True


# Test for ping_pacs function with 404 error
@patch("imaging_api.services.imaging.requests.get")
def test_ping_pacs_not_found(mock_get):
    pacs_id = 2
    headers = {}

    # Configure the mock to raise a NotFoundError
    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.reason = "Not Found"
    mock_get.return_value = mock_response

    # Call the function and check for NotFoundError
    with pytest.raises(NotFoundError) as excinfo:
        ping_pacs(pacs_id, headers)

    assert f"PACS with ID '{pacs_id}' not found." in str(excinfo.value)


@patch("imaging_api.services.imaging.requests.post")
def test_query_by_accession_number(mock_post, headers):
    accession_number = "FAK57777617"

    # Define the mock response data
    mock_response_data = [
        {
            "accessionNumber": accession_number,
            "studyInstanceUid": "1.2.3.4",
            "studyDate": "2023-01-01",
            "studyDescription": "Test Study",
            "modalitiesInStudy": ["CT", "MR"],
            "referringPhysicianName": "Dr. Smith",
            "patient": {"id": "12345", "name": "John Doe", "sex": "M"},
        }
    ]

    # Configure the mock
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = mock_response_data
    mock_response.text = str(mock_response_data)
    mock_response.reason = "OK"

    # Assign the mock response to requests.post
    mock_post.return_value = mock_response

    # Call the function
    studies = query_by_accession_number(accession_number, headers)

    # Assertions
    assert len(studies) == 1
    assert studies[0].accession_number == accession_number


@patch("imaging_api.services.imaging.check_pacs")
@patch("imaging_api.services.imaging.requests.post")
@patch("imaging_api.services.imaging.get_project")
def test_queue_image_import_request(mock_check_pacs, mock_requests_post, mock_get_project, headers):
    # Mock checking if the PACS is reachable
    mock_check_pacs.return_value = None

    # Mock checking if the project exists
    # This is only used to check that the project exists
    mock_get_project.return_value = None

    # Mock DQR import POST request
    # Prepare the JSON payload we expect back
    mocked_payload = [
        {
            "id": 1,
            "pacsId": 1,
            "status": "QUEUED",
            "accessionNumber": "FAK57777617",
            "queuedTime": 123456789,
            "created": 123456789,
            "priority": 1,
        }
    ]

    # Mock POST response with .text containing JSON
    mock_requests_post.return_value.status_code = 200
    mock_requests_post.return_value.text = json.dumps(mocked_payload)

    # Test data for ImportStudyRequest
    studies = [
        {
            "studyInstanceUid": "1.2.826.0.1.3680043.8.274.1.1.8323329.1189734.1740750875.622774",
            "accessionNumber": "FAK57777617",
        },
    ]
    import_request = ImportStudyRequest(projectId="test", studies=studies)

    response = queue_image_import_request(import_request, headers)

    # Assertions
    assert response[0].status == "QUEUED"
    assert response[0].pacs_id == 1
