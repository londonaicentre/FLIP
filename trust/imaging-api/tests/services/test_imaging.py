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
import re
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from imaging_api.config import get_settings
from imaging_api.routers.schemas import ImportStudyRequest
from imaging_api.services.imaging import (
    XNAT_DQR_REQUEST_TIMEOUT,
    XNAT_REQUEST_TIMEOUT,
    check_pacs,
    ping_pacs,
    ping_registered_pacs,
    query_by_accession_number,
    queue_image_import_request,
    resolve_default_pacs_id,
)
from imaging_api.utils.exceptions import NotFoundError, XnatFetchError


@pytest.fixture
def headers():
    return {}


@pytest.fixture
def default_pacs_resolved() -> Iterator[MagicMock]:
    """Stands in for the default-PACS lookup, for import tests about what happens after it."""
    with patch("imaging_api.services.imaging.resolve_default_pacs_id", return_value=1) as resolved:
        yield resolved


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
    # Bounded, so a wedged XNAT fails the health probe instead of hanging it
    assert mock_get.call_args.kwargs["timeout"] == XNAT_REQUEST_TIMEOUT


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


@pytest.mark.parametrize(
    "descriptive_tags",
    [
        {"studyDescription": "Test Study", "referringPhysicianName": "Dr. Smith"},
        {"studyDescription": "Test Study"},
        {"referringPhysicianName": "Dr. Smith"},
        {},
    ],
    ids=["complete", "no-referrer", "no-description", "no-descriptive-tags"],
)
@patch("imaging_api.services.imaging.requests.post")
def test_query_by_accession_number(mock_post, headers, descriptive_tags):
    accession_number = "FAK57777617"

    # Define the mock response data
    mock_response_data = [
        {
            "accessionNumber": accession_number,
            "studyInstanceUid": "1.2.3.4",
            "studyDate": "2023-01-01",
            "modalitiesInStudy": ["CT", "MR"],
            "patient": {"id": "12345", "name": "John Doe", "sex": "M"},
            **descriptive_tags,
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
    assert studies[0].study_instance_uid == "1.2.3.4"
    assert studies[0].study_description == descriptive_tags.get("studyDescription", "")
    assert studies[0].referring_physician_name == descriptive_tags.get("referringPhysicianName", "")
    # Bounded, so a wedged XNAT fails the query instead of hanging the import path
    assert mock_post.call_args.kwargs["timeout"] == XNAT_DQR_REQUEST_TIMEOUT


@patch("imaging_api.services.imaging.requests.get")
@patch("imaging_api.services.imaging.requests.post")
def test_query_by_accession_number_names_no_pacs(mock_post, mock_get, headers):
    """DQR queries its default PACS when the body has no pacsId, so imaging-api resolves nothing for a query."""
    mock_post.return_value = MagicMock(status_code=204, reason="No Content")

    query_by_accession_number("ACC1", headers)

    assert mock_post.call_args.kwargs["json"] == {"accessionNumber": "ACC1", "modality": ""}
    assert not mock_get.called, "a query must not list XNAT's PACS: DQR picks the default itself"


@patch("imaging_api.services.imaging.check_pacs")
@patch("imaging_api.services.imaging.requests.post")
@patch("imaging_api.services.imaging.get_project")
def test_queue_image_import_request(
    mock_get_project, mock_requests_post, mock_check_pacs, default_pacs_resolved, headers
):
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
    # Bounded, so a wedged XNAT fails the import instead of hanging it
    assert mock_requests_post.call_args.kwargs["timeout"] == XNAT_DQR_REQUEST_TIMEOUT


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

    # The status is kept: with no pacsId in the query, it is the only trace of DQR lacking a default PACS
    with pytest.raises(Exception, match=re.escape("Failed to query PACS via DQR (HTTP 500)")):
        query_by_accession_number("ACC123", headers)


# ---------------------------------------------------------------------------
# queue_image_import_request — 404 Not Found from DQR
# ---------------------------------------------------------------------------
@patch("imaging_api.services.imaging.check_pacs")
@patch("imaging_api.services.imaging.requests.post")
@patch("imaging_api.services.imaging.get_project")
def test_queue_image_import_request_dqr_404(
    mock_get_project, mock_post, mock_check_pacs, default_pacs_resolved, headers
):
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
def test_queue_image_import_request_dqr_500(
    mock_get_project, mock_post, mock_check_pacs, default_pacs_resolved, headers
):
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
def test_queue_image_import_request_empty_response(
    mock_get_project, mock_post, mock_check_pacs, default_pacs_resolved, headers
):
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
def test_queue_image_import_request_mismatched_count(
    mock_get_project, mock_post, mock_check_pacs, default_pacs_resolved, headers
):
    mock_get_project.return_value = None
    mock_check_pacs.return_value = None

    # Return only 1 response for 2 requested studies
    mock_post.return_value = MagicMock(
        status_code=200,
        text=json.dumps(
            [
                {
                    "id": 1,
                    "pacsId": 1,
                    "status": "QUEUED",
                    "accessionNumber": "ACC1",
                    "queuedTime": 100,
                    "created": 100,
                    "priority": 1,
                },
            ]
        ),
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
def test_queue_image_import_request_partial_failure(
    mock_get_project, mock_post, mock_check_pacs, default_pacs_resolved, headers
):
    mock_get_project.return_value = None
    mock_check_pacs.return_value = None

    payload = [
        {
            "id": 1,
            "pacsId": 1,
            "status": "QUEUED",
            "accessionNumber": "ACC1",
            "queuedTime": 100,
            "created": 100,
            "priority": 1,
        },
        {
            "id": 2,
            "pacsId": 1,
            "status": "FAILED",
            "accessionNumber": "ACC2",
            "queuedTime": 101,
            "created": 101,
            "priority": 1,
        },
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


# --- The default query/retrieve PACS, read from XNAT per call (FLIP#1456 E1) ---------


def _pacs(pacs_id: int, ae_title: str, default: bool = True) -> dict[str, Any]:
    """One PACS registration as XNAT's ``GET /xapi/pacs`` lists it (the fields imaging-api reads)."""
    return {"id": pacs_id, "aeTitle": ae_title, "defaultQueryRetrievePacs": default, "queryable": True, "enabled": True}


def _status(pacs_id: int) -> dict[str, Any]:
    return {
        "pacsId": pacs_id,
        "successful": True,
        "pingTime": 123,
        "created": 1610000000,
        "enabled": True,
        "timestamp": 1610001234,
        "id": pacs_id,
        "disabled": 0,
    }


class _FakeXnat:
    """A ``requests.get`` stand-in for XNAT's PACS endpoints that records each listing and ping it serves.

    Successive listings are served in order and the last one repeats, so a test can re-register the
    PACS under a new id between two calls.
    """

    def __init__(self, *listings: list[dict[str, Any]]) -> None:
        self._listings = list(listings)
        self.listing_queries: list[dict[str, list[str]]] = []
        self.pinged: list[int] = []

    def get(self, url: str, **kwargs: Any) -> MagicMock:
        parts = urlsplit(url)
        path = parts.path.split("/")
        if path[-2:] == ["xapi", "pacs"]:
            self.listing_queries.append(parse_qs(parts.query))
            listing = self._listings.pop(0) if len(self._listings) > 1 else self._listings[0]
            return MagicMock(status_code=200, json=lambda: listing)
        if path[-1] == "status":
            pacs_id = int(path[-2])
            self.pinged.append(pacs_id)
            return MagicMock(status_code=200, json=lambda: _status(pacs_id))
        raise AssertionError(f"unexpected XNAT GET {url}")


def _import_request(pacs_id: int | None = None) -> ImportStudyRequest:
    return ImportStudyRequest.model_validate(
        {
            "projectId": "test",
            "pacsId": pacs_id,
            "studies": [{"studyInstanceUid": "1.2.3.4", "accessionNumber": "ACC1"}],
        }
    )


def _queued(pacs_id: int) -> MagicMock:
    """DQR's 200 reply to an import of the one study ``_import_request`` names."""
    queued = {
        "id": 1,
        "pacsId": pacs_id,
        "status": "QUEUED",
        "accessionNumber": "ACC1",
        "queuedTime": 100,
        "created": 100,
        "priority": 1,
    }
    return MagicMock(status_code=200, text=json.dumps([queued]))


def test_resolve_default_pacs_id_reads_the_flagged_pacs_from_the_queryable_listing(headers):
    """The id is XNAT's, from the queryable listing, and the default flag — not list order — picks it."""
    xnat = _FakeXnat([_pacs(3, "STRAY", default=False), _pacs(9, "SECTRA_QR")])

    with patch("imaging_api.services.imaging.requests.get", side_effect=xnat.get) as mock_get:
        assert resolve_default_pacs_id(headers) == 9

    assert xnat.listing_queries == [{"queryable": ["true"]}]
    assert mock_get.call_args.kwargs["timeout"] == XNAT_REQUEST_TIMEOUT


def test_resolve_default_pacs_id_reads_xnat_on_every_call(headers):
    """No module-level cache: a PACS re-registered under a new id is found on the very next call."""
    xnat = _FakeXnat([_pacs(7, "SECTRA_QR")], [_pacs(9, "SECTRA_QR")])

    with patch("imaging_api.services.imaging.requests.get", side_effect=xnat.get):
        assert resolve_default_pacs_id(headers) == 7
        assert resolve_default_pacs_id(headers) == 9

    assert len(xnat.listing_queries) == 2


@pytest.mark.parametrize(
    ("listing", "found"),
    [([], "none"), ([_pacs(3, "STRAY", default=False)], "'STRAY' (id 3)")],
    ids=["nothing registered", "registered but not the default"],
)
def test_resolve_default_pacs_id_without_a_default_fails_naming_what_was_found(listing, found, headers):
    """No guessed fallback id: an XNAT without a default PACS is reported as exactly that."""
    xnat = _FakeXnat(listing)

    with (
        patch("imaging_api.services.imaging.requests.get", side_effect=xnat.get),
        pytest.raises(NotFoundError) as excinfo,
    ):
        resolve_default_pacs_id(headers)

    assert "no PACS flagged as the default query/retrieve PACS" in str(excinfo.value)
    assert f"(queryable PACS found: {found})" in str(excinfo.value)
    # The advice: the configure step restores a missing flag, but cannot re-enable a disabled PACS,
    # which DQR leaves out of the listing altogether.
    assert "restores the flag" in str(excinfo.value)
    assert "configure step does not re-enable it" in str(excinfo.value)


def test_resolve_default_pacs_id_with_two_defaults_fails_naming_both(headers):
    """Two flagged PACS make DQR's own choice ambiguous, so neither is picked."""
    xnat = _FakeXnat([_pacs(3, "ORTHANC"), _pacs(9, "SECTRA_QR")])

    named = "flags 2 PACS as the default query/retrieve PACS ('ORTHANC' (id 3), 'SECTRA_QR' (id 9))"
    with (
        patch("imaging_api.services.imaging.requests.get", side_effect=xnat.get),
        pytest.raises(Exception, match=re.escape(named)) as excinfo,
    ):
        resolve_default_pacs_id(headers)

    assert excinfo.type is Exception, "two defaults is a misconfiguration, not a missing PACS (404)"


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (MagicMock(status_code=500), "HTTP 500"),
        (MagicMock(status_code=200, json=MagicMock(side_effect=ValueError("no JSON"))), "not JSON"),
        (MagicMock(status_code=200, json=lambda: {"id": 1}), "not a list"),
        (MagicMock(status_code=200, json=lambda: [1]), "not a list"),
        (MagicMock(status_code=200, json=lambda: [{"aeTitle": "SECTRA_QR", "defaultQueryRetrievePacs": True}]), "id"),
        (
            MagicMock(status_code=200, json=lambda: [{"id": None, "defaultQueryRetrievePacs": True}]),
            "no usable id",
        ),
    ],
    ids=["http error", "not json", "object", "not registrations", "no id", "null id"],
)
def test_resolve_default_pacs_id_fails_on_a_listing_it_cannot_use(response, message, headers):
    """Anything but a usable listing is an XNAT failure (500), never a reason to guess an id."""
    with (
        patch("imaging_api.services.imaging.requests.get", return_value=response),
        pytest.raises(XnatFetchError, match=message),
    ):
        resolve_default_pacs_id(headers)


def test_resolve_default_pacs_id_fails_when_xnat_is_unreachable(headers):
    with (
        patch("imaging_api.services.imaging.requests.get", side_effect=requests.ConnectionError("refused")),
        pytest.raises(XnatFetchError, match="Could not list the PACS registered in XNAT: refused"),
    ):
        resolve_default_pacs_id(headers)


@patch("imaging_api.services.imaging.requests.post")
@patch("imaging_api.services.imaging.get_project")
def test_import_resolves_the_default_pacs_once_per_import_and_sends_its_id(mock_get_project, mock_post, headers):
    """One listing per import, its id pinged and sent to DQR — whatever the caller put in pacsId.

    The second import runs after the PACS was re-registered under a new id: it must use the new id,
    so nothing from the first import is held over.
    """
    xnat = _FakeXnat([_pacs(7, "SECTRA_QR")], [_pacs(9, "SECTRA_QR")])
    mock_post.side_effect = [_queued(7), _queued(9)]

    with patch("imaging_api.services.imaging.requests.get", side_effect=xnat.get):
        queue_image_import_request(_import_request(pacs_id=5), headers)
        queue_image_import_request(_import_request(), headers)

    assert len(xnat.listing_queries) == 2, "expected exactly one PACS listing per import"
    assert xnat.pinged == [7, 9]
    assert [c.kwargs["json"]["pacsId"] for c in mock_post.call_args_list] == [7, 9]


@pytest.mark.parametrize(
    "listing",
    [[], [_pacs(3, "STRAY", default=False)], [_pacs(3, "ORTHANC"), _pacs(9, "SECTRA_QR")]],
    ids=["no PACS", "no default", "two defaults"],
)
@patch("imaging_api.services.imaging.requests.post")
@patch("imaging_api.services.imaging.get_project")
def test_import_without_exactly_one_default_pacs_fails_and_imports_nothing(
    mock_get_project, mock_post, listing, headers
):
    """No static fallback: the import stops with the resolution error and DQR is never asked to import."""
    xnat = _FakeXnat(listing)

    with (
        patch("imaging_api.services.imaging.requests.get", side_effect=xnat.get),
        pytest.raises(Exception, match="default query/retrieve PACS"),
    ):
        queue_image_import_request(_import_request(), headers)

    assert not mock_post.called, "an import was sent without a resolved PACS"
    assert xnat.pinged == []


def test_ping_registered_pacs_pings_the_default_pacs_resolved_on_every_call(headers):
    """trust-api's health probe: a PACS re-registered under a new id is pinged at that id next time."""
    xnat = _FakeXnat([_pacs(7, "SECTRA_QR")], [_pacs(9, "SECTRA_QR")])

    with patch("imaging_api.services.imaging.requests.get", side_effect=xnat.get):
        assert ping_registered_pacs(headers).pacs_id == 7
        assert ping_registered_pacs(headers).pacs_id == 9

    assert xnat.pinged == [7, 9]
    assert len(xnat.listing_queries) == 2


def test_ping_registered_pacs_without_a_default_raises_not_found_and_pings_nothing(headers):
    """The router turns this into the 404 the probe already treated as "unknown"."""
    xnat = _FakeXnat([])

    with (
        patch("imaging_api.services.imaging.requests.get", side_effect=xnat.get),
        pytest.raises(NotFoundError),
    ):
        ping_registered_pacs(headers)

    assert xnat.pinged == []


def test_import_request_ae_title_follows_settings():
    """The C-MOVE destination AE title is configuration, not a hardcoded literal."""
    from imaging_api.config import get_settings

    request = ImportStudyRequest(
        projectId="project-1",
        studies=[{"studyInstanceUid": "1.2.3", "accessionNumber": "ACC1"}],
    )

    assert request.ae_title == get_settings().XNAT_AETITLE


# --- Request URLs go through xnat_url (#1386) -----------------------------------------


def test_pacs_urls_are_unchanged_and_take_an_integer_id(sent_xnat_requests):
    """The PACS id is an int; it reaches the path as its decimal string, exactly as the f-string put it."""
    status = {
        "id": 1,
        "pacsId": 3,
        "successful": True,
        "pingTime": 1,
        "created": 1,
        "enabled": True,
        "disabled": 0,
        "timestamp": 1,
    }
    with patch.object(requests.Response, "json", side_effect=[[{"id": 3, "defaultQueryRetrievePacs": True}], status]):
        pacs_id = resolve_default_pacs_id({})
        ping_pacs(pacs_id, {})

    xnat = get_settings().XNAT_URL
    assert [(r.method, r.url) for r in sent_xnat_requests] == [
        ("GET", requests.Request("GET", f"{xnat}/xapi/pacs", params={"queryable": "true"}).prepare().url),
        ("GET", requests.Request("GET", f"{xnat}/xapi/pacs/3/status").prepare().url),
    ]
