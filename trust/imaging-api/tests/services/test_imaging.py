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
from imaging_api.services.imaging import check_pacs, ping_pacs, query_by_accession_number, queue_image_import_request
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


# ---------------------------------------------------------------------------
# ping_pacs — server error
# ---------------------------------------------------------------------------
@patch("imaging_api.services.imaging.requests.get")
def test_ping_pacs_server_error(mock_get):
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "Internal Server Error"
    mock_get.return_value = mock_response

    with pytest.raises(Exception, match="Failed to ping PACS"):
        ping_pacs(1, {})


# ---------------------------------------------------------------------------
# check_pacs — happy path
# ---------------------------------------------------------------------------
@patch("imaging_api.services.imaging.ping_pacs")
def test_check_pacs_success(mock_ping):
    mock_ping.return_value = MagicMock(successful=True, enabled=True)
    check_pacs({}, pacs_id=1)  # should not raise


# ---------------------------------------------------------------------------
# check_pacs — not found
# ---------------------------------------------------------------------------
@patch("imaging_api.services.imaging.ping_pacs")
def test_check_pacs_not_found(mock_ping):
    mock_ping.side_effect = NotFoundError("PACS with ID '1' not found.")

    with pytest.raises(NotFoundError, match="not found"):
        check_pacs({}, pacs_id=1)


# ---------------------------------------------------------------------------
# check_pacs — ping fails with generic error
# ---------------------------------------------------------------------------
@patch("imaging_api.services.imaging.ping_pacs")
def test_check_pacs_ping_error(mock_ping):
    mock_ping.side_effect = Exception("connection refused")

    with pytest.raises(Exception, match="Failed to ping PACS"):
        check_pacs({}, pacs_id=1)


# ---------------------------------------------------------------------------
# check_pacs — pacs not reachable (successful=False)
# ---------------------------------------------------------------------------
@patch("imaging_api.services.imaging.ping_pacs")
def test_check_pacs_not_reachable(mock_ping):
    mock_ping.return_value = MagicMock(successful=False, enabled=True)

    with pytest.raises(Exception, match="is not reachable"):
        check_pacs({}, pacs_id=1)


# ---------------------------------------------------------------------------
# check_pacs — pacs disabled
# ---------------------------------------------------------------------------
@patch("imaging_api.services.imaging.ping_pacs")
def test_check_pacs_disabled(mock_ping):
    mock_ping.return_value = MagicMock(successful=True, enabled=False)

    with pytest.raises(Exception, match="is disabled"):
        check_pacs({}, pacs_id=1)


# ---------------------------------------------------------------------------
# query_by_accession_number — 204 No Content
# ---------------------------------------------------------------------------
@patch("imaging_api.services.imaging.requests.post")
def test_query_by_accession_number_no_content(mock_post, headers):
    mock_response = MagicMock()
    mock_response.status_code = 204
    mock_response.text = ""
    mock_response.reason = "No Content"
    mock_post.return_value = mock_response

    studies = query_by_accession_number("MISSING123", headers)
    assert studies == []


# ---------------------------------------------------------------------------
# query_by_accession_number — 401 Unauthorized
# ---------------------------------------------------------------------------
@patch("imaging_api.services.imaging.requests.post")
def test_query_by_accession_number_unauthorized(mock_post, headers):
    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.text = "Unauthorized"
    mock_response.reason = "Unauthorized"
    mock_post.return_value = mock_response

    with pytest.raises(Exception, match="Unauthorized"):
        query_by_accession_number("ACC123", headers)


# ---------------------------------------------------------------------------
# query_by_accession_number — 500 Server Error
# ---------------------------------------------------------------------------
@patch("imaging_api.services.imaging.requests.post")
def test_query_by_accession_number_server_error(mock_post, headers):
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "Server Error"
    mock_response.reason = "Internal Server Error"
    mock_post.return_value = mock_response

    with pytest.raises(Exception, match="Failed to query PACS"):
        query_by_accession_number("ACC123", headers)


# ---------------------------------------------------------------------------
# queue_image_import_request — 404 Not Found from DQR
# ---------------------------------------------------------------------------
@patch("imaging_api.services.imaging.check_pacs")
@patch("imaging_api.services.imaging.requests.post")
@patch("imaging_api.services.imaging.get_project")
def test_queue_image_import_request_dqr_404(mock_get_project, mock_post, mock_check_pacs, headers):
    mock_get_project.return_value = None
    mock_check_pacs.return_value = None

    mock_post.return_value = MagicMock(status_code=404, text="Not found")

    studies = [{"studyInstanceUid": "1.2.3.4", "accessionNumber": "ACC1"}]
    import_request = ImportStudyRequest(projectId="test", studies=studies)

    with pytest.raises(NotFoundError, match="Not found error"):
        queue_image_import_request(import_request, headers)


# ---------------------------------------------------------------------------
# queue_image_import_request — 500 Server Error from DQR
# ---------------------------------------------------------------------------
@patch("imaging_api.services.imaging.check_pacs")
@patch("imaging_api.services.imaging.requests.post")
@patch("imaging_api.services.imaging.get_project")
def test_queue_image_import_request_dqr_500(mock_get_project, mock_post, mock_check_pacs, headers):
    mock_get_project.return_value = None
    mock_check_pacs.return_value = None

    mock_post.return_value = MagicMock(status_code=500, text="Server Error")

    studies = [{"studyInstanceUid": "1.2.3.4", "accessionNumber": "ACC1"}]
    import_request = ImportStudyRequest(projectId="test", studies=studies)

    with pytest.raises(Exception, match="Failed to queue image import"):
        queue_image_import_request(import_request, headers)


# ---------------------------------------------------------------------------
# queue_image_import_request — empty response (no studies found on PACS)
# ---------------------------------------------------------------------------
@patch("imaging_api.services.imaging.check_pacs")
@patch("imaging_api.services.imaging.requests.post")
@patch("imaging_api.services.imaging.get_project")
def test_queue_image_import_request_empty_response(mock_get_project, mock_post, mock_check_pacs, headers):
    mock_get_project.return_value = None
    mock_check_pacs.return_value = None

    mock_post.return_value = MagicMock(status_code=200, text="[]")

    studies = [{"studyInstanceUid": "1.2.3.4", "accessionNumber": "ACC1"}]
    import_request = ImportStudyRequest(projectId="test", studies=studies)

    with pytest.raises(NotFoundError, match="No studies found on PACS"):
        queue_image_import_request(import_request, headers)


# ---------------------------------------------------------------------------
# queue_image_import_request — mismatched study count
# ---------------------------------------------------------------------------
@patch("imaging_api.services.imaging.check_pacs")
@patch("imaging_api.services.imaging.requests.post")
@patch("imaging_api.services.imaging.get_project")
def test_queue_image_import_request_mismatched_count(mock_get_project, mock_post, mock_check_pacs, headers):
    mock_get_project.return_value = None
    mock_check_pacs.return_value = None

    # Return only 1 response for 2 requested studies
    mock_post.return_value = MagicMock(
        status_code=200,
        text=json.dumps([
            {"id": 1, "pacsId": 1, "status": "QUEUED", "accessionNumber": "ACC1",
             "queuedTime": 100, "created": 100, "priority": 1},
        ]),
    )

    studies = [
        {"studyInstanceUid": "1.2.3.4", "accessionNumber": "ACC1"},
        {"studyInstanceUid": "5.6.7.8", "accessionNumber": "ACC2"},
    ]
    import_request = ImportStudyRequest(projectId="test", studies=studies)

    with pytest.raises(ValueError, match="Some studies not found on PACS"):
        queue_image_import_request(import_request, headers)


# ---------------------------------------------------------------------------
# queue_image_import_request — partial queue failure
# ---------------------------------------------------------------------------
@patch("imaging_api.services.imaging.check_pacs")
@patch("imaging_api.services.imaging.requests.post")
@patch("imaging_api.services.imaging.get_project")
def test_queue_image_import_request_partial_failure(mock_get_project, mock_post, mock_check_pacs, headers):
    mock_get_project.return_value = None
    mock_check_pacs.return_value = None

    payload = [
        {"id": 1, "pacsId": 1, "status": "QUEUED", "accessionNumber": "ACC1",
         "queuedTime": 100, "created": 100, "priority": 1},
        {"id": 2, "pacsId": 1, "status": "FAILED", "accessionNumber": "ACC2",
         "queuedTime": 101, "created": 101, "priority": 1},
    ]
    mock_post.return_value = MagicMock(status_code=200, text=json.dumps(payload))

    studies = [
        {"studyInstanceUid": "1.2.3.4", "accessionNumber": "ACC1"},
        {"studyInstanceUid": "5.6.7.8", "accessionNumber": "ACC2"},
    ]
    import_request = ImportStudyRequest(projectId="test", studies=studies)

    response = queue_image_import_request(import_request, headers)
    assert response[0].status == "QUEUED"
    assert response[1].status == "FAILED"
