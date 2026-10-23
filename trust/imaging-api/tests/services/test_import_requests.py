# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
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

"""The REST reads that replaced imaging-api's queries on XNAT's database (FLIP#1456 B1).

Requests are prepared for real and only the network dispatch is replaced, so the URL, its query
string and the POST body are checked as XNAT would receive them. Responses are shaped like the JSON
DQR 3.0.0 and xnat-web 1.10.0 serve: camelCase fields, epoch-millisecond times, null fields left out.
"""

import json
import logging
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from imaging_api.services import import_requests
from imaging_api.services.import_requests import (
    DirectArchiveSession,
    PacsRequest,
    archive_prearchive_session,
    delete_prearchive_session,
    get_direct_archive_sessions,
    get_executed_import_requests,
    get_prearchive_sessions,
    get_queued_import_requests,
)
from imaging_api.utils.exceptions import NotFoundError, XnatFetchError
from imaging_api.utils.xnat_url import XNAT_REQUEST_TIMEOUT

PROJECT = "b4a5fa39-004c-4522-b808-8bdf92b70ccb"
OTHER = "46c30776-3495-404b-ad03-fb71e0379b15"
PAGE = import_requests._PAGE_SIZE

# The project's creation as XNAT reports it (server wall-clock, no zone), and the horizon a day before.
INSERT_DATE = "2026-10-12 18:52:11.266"
CREATED_MS = int(datetime(2026, 10, 12, 18, 52, 11, 266000, tzinfo=UTC).timestamp() * 1000)
HORIZON_MS = CREATED_MS - int(timedelta(days=1).total_seconds() * 1000)
# XNAT names the requested column in lower case.
_COL = "xnat:projectdata/meta/insert_date"


def _request(request_id: int, project: str = PROJECT, accession: str = "ACC", status: str = "RECEIVED", **times: int):
    """A DQR request as the queue or history serves it."""
    row: dict[str, Any] = {"id": request_id, "xnatProject": project, "accessionNumber": accession, "status": status}
    stamp = times.pop("at", CREATED_MS + request_id)
    row.update({"queuedTime": stamp, "created": stamp, "executedTime": stamp, "timestamp": stamp})
    row.update(times)
    return row


def _projects_listing(*rows: dict[str, Any]) -> dict[str, Any]:
    return {"ResultSet": {"Result": list(rows)}}


def _path(request: requests.PreparedRequest) -> str:
    return urlsplit(str(request.url)).path


def _query(request: requests.PreparedRequest) -> dict[str, list[str]]:
    return parse_qs(urlsplit(str(request.url)).query)


def _body(request: requests.PreparedRequest) -> Any:
    return json.loads(request.body or b"null")


class FakeXnat:
    """Answers prepared requests by path, and records them."""

    def __init__(self) -> None:
        self.sent: list[requests.PreparedRequest] = []
        self.routes: dict[tuple[str, str], Callable[[requests.PreparedRequest], tuple[int, Any]]] = {}
        # By default XNAT knows when the project was created.
        self.on("GET", "/data/projects", lambda r: (200, _projects_listing({"ID": PROJECT, _COL: INSERT_DATE})))

    def on(self, method: str, path: str, handler: Callable[[requests.PreparedRequest], tuple[int, Any]]) -> None:
        self.routes[(method, path)] = handler

    def pages(self, method: str, path: str, pages: list[list[dict[str, Any]]]) -> None:
        """Serve ``pages`` by the page number in the query (GET) or the body (POST); past the end is empty."""

        def handler(request: requests.PreparedRequest) -> tuple[int, Any]:
            if method == "GET":
                number = int(_query(request)["page"][0])
            else:
                number = _body(request)["page"]
            return 200, pages[number - 1] if number <= len(pages) else []

        self.on(method, path, handler)

    def requested(self, method: str, path: str) -> list[requests.PreparedRequest]:
        return [r for r in self.sent if r.method == method and _path(r) == path]

    def respond(self, request: requests.PreparedRequest, **kwargs: Any) -> requests.Response:
        self.sent.append(request)
        status, body = self.routes[(str(request.method), _path(request))](request)
        response = requests.Response()
        response.status_code = status
        response._content = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        setattr(response, "_content_consumed", True)  # private requests field, absent from the stubs
        response.request = request
        return response


@pytest.fixture
def xnat() -> Iterator[FakeXnat]:
    fake = FakeXnat()
    with patch.object(requests.Session, "send", side_effect=fake.respond):
        yield fake


# ===========================================================================
# Queue
# ===========================================================================


def test_queue_keeps_only_the_projects_requests(xnat):
    xnat.pages("GET", "/xapi/dqr/import/queue", [[_request(3, accession="A3"), _request(2, OTHER, "B2"), _request(1)]])

    queued = get_queued_import_requests(PROJECT, {})

    assert [(r.id, r.xnat_project, r.accession_number, r.status) for r in queued] == [
        (3, PROJECT, "A3", "RECEIVED"),
        (1, PROJECT, "ACC", "RECEIVED"),
    ]


def test_queue_asks_for_every_users_requests_newest_first(xnat):
    """``all=true``: the table read returned every user's rows, and so must this."""
    xnat.pages("GET", "/xapi/dqr/import/queue", [[]])

    get_queued_import_requests(PROJECT, {})

    assert _query(xnat.requested("GET", "/xapi/dqr/import/queue")[0]) == {
        "all": ["true"],
        "sort": ["desc"],
        "page": ["1"],
        "pageSize": [str(PAGE)],
    }


def test_queue_request_fields_may_be_absent():
    """DQR leaves null fields out of its JSON; only the id is needed."""
    assert PacsRequest.model_validate({"id": 7}) == PacsRequest(id=7)


def test_queue_reads_every_page(xnat):
    first = [_request(i, accession=f"A{i}") for i in range(2 * PAGE, PAGE, -1)]
    xnat.pages("GET", "/xapi/dqr/import/queue", [first, [_request(1, accession="A1")]])

    queued = get_queued_import_requests(PROJECT, {})

    assert [r.id for r in queued] == [r["id"] for r in first] + [1]
    assert [_query(r)["page"] for r in xnat.requested("GET", "/xapi/dqr/import/queue")] == [["1"], ["2"]]


def test_a_request_pushed_onto_the_next_page_is_kept_once(xnat):
    """A request queued between two page reads moves the rest down one, repeating one on the next page."""
    first = [_request(i) for i in range(2 * PAGE, PAGE, -1)]
    second = [_request(PAGE + 1), _request(5)]
    xnat.pages("GET", "/xapi/dqr/import/queue", [first, second])

    ids = [r.id for r in get_queued_import_requests(PROJECT, {})]

    assert len(ids) == len(set(ids)) == PAGE + 1


def test_a_server_ignoring_the_page_number_is_a_fetch_error(xnat):
    """Stopping there would drop the rest of the listing and report its accessions as never queued."""
    full_page = [_request(i) for i in range(PAGE, 0, -1)]
    xnat.on("GET", "/xapi/dqr/import/queue", lambda r: (200, full_page))

    with pytest.raises(XnatFetchError, match="page 2 repeats the pages before it"):
        get_queued_import_requests(PROJECT, {})


# ===========================================================================
# Failures: a 500 to the caller, never the 404 the hub reads as a missing project
# ===========================================================================


@pytest.mark.parametrize("status", [404, 401, 500])
def test_an_unreadable_listing_is_a_fetch_error_not_a_not_found(xnat, status):
    xnat.on("GET", "/xapi/dqr/import/queue", lambda r: (status, "<html>Status page</html>"))

    with pytest.raises(XnatFetchError, match=f"DQR import queue fetch failed: {status}") as raised:
        get_queued_import_requests(PROJECT, {})
    assert not isinstance(raised.value, NotFoundError)


@pytest.mark.parametrize("listing", ["queue", "history"])
def test_a_403_names_the_missing_all_data_access(xnat, listing):
    xnat.on("GET", f"/xapi/dqr/import/{listing}", lambda r: (403, "You must have all data access privileges"))
    read = get_queued_import_requests if listing == "queue" else get_executed_import_requests

    with pytest.raises(XnatFetchError, match="403 - the service account lacks all-data access$"):
        read(PROJECT, {})


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ("<html>login</html>", "not JSON"),
        ({"ResultSet": {}}, "not a list of records"),
        ([1, 2], "not a list of records"),
    ],
)
def test_a_body_that_is_not_a_list_of_records_is_a_fetch_error(xnat, body, reason):
    xnat.on("GET", "/xapi/dqr/import/history", lambda r: (200, body))

    with pytest.raises(XnatFetchError, match=reason):
        get_executed_import_requests(PROJECT, {})


def test_a_transport_failure_is_a_fetch_error(xnat):
    def refuse(request):
        raise requests.ConnectionError("connection refused")

    xnat.on("GET", "/xapi/dqr/import/queue", refuse)

    with pytest.raises(XnatFetchError, match="connection refused"):
        get_queued_import_requests(PROJECT, {})


def test_an_error_body_is_bounded_in_the_message(xnat):
    xnat.on("GET", "/xapi/dqr/import/queue", lambda r: (500, "x" * 5000))

    with pytest.raises(XnatFetchError) as raised:
        get_queued_import_requests(PROJECT, {})
    assert len(str(raised.value)) < 300


# ===========================================================================
# History
# ===========================================================================


def test_history_keeps_only_the_projects_requests(xnat):
    xnat.pages(
        "GET",
        "/xapi/dqr/import/history",
        [[_request(3, accession="A3", status="FAILED"), _request(2, OTHER, "B2", status="FAILED"), _request(1)]],
    )

    executed = get_executed_import_requests(PROJECT, {})

    assert [(r.accession_number, r.status) for r in executed] == [("A3", "FAILED"), ("ACC", "RECEIVED")]
    assert _query(xnat.requested("GET", "/xapi/dqr/import/history")[0]) == {
        "all": ["true"],
        "sort": ["desc"],
        "page": ["1"],
        "pageSize": [str(PAGE)],
    }


def test_history_stops_once_a_page_reaches_back_past_the_project(xnat):
    """Read newest first, nothing after a request older than the horizon can be the project's."""
    first = [_request(i) for i in range(2 * PAGE, PAGE + 1, -1)] + [_request(PAGE + 1, OTHER, at=HORIZON_MS - 1)]
    xnat.pages("GET", "/xapi/dqr/import/history", [first, [_request(1)]])

    executed = get_executed_import_requests(PROJECT, {})

    assert len(xnat.requested("GET", "/xapi/dqr/import/history")) == 1
    assert len(executed) == PAGE - 1


def test_history_reads_on_while_the_page_is_inside_the_horizon(xnat):
    first = [_request(i) for i in range(2 * PAGE, PAGE, -1)]
    # Queued and executed before the horizon, but last updated after it: the read goes on.
    first[-1].update(queuedTime=HORIZON_MS - 1, created=HORIZON_MS - 1, executedTime=HORIZON_MS - 1)
    xnat.pages("GET", "/xapi/dqr/import/history", [first, [_request(1, at=HORIZON_MS)]])

    executed = get_executed_import_requests(PROJECT, {})

    assert len(xnat.requested("GET", "/xapi/dqr/import/history")) == 2
    assert executed[-1].id == 1


def test_history_keeps_a_matching_request_on_the_last_page_read(xnat):
    """The horizon only decides when to stop fetching; every row fetched is still filtered by project."""
    first = [_request(i) for i in range(2 * PAGE, PAGE + 1, -1)] + [_request(PAGE + 1, at=HORIZON_MS - 1)]
    xnat.pages("GET", "/xapi/dqr/import/history", [first])

    assert get_executed_import_requests(PROJECT, {})[-1].id == PAGE + 1


@pytest.mark.parametrize(
    "row",
    [{"ID": OTHER, _COL: INSERT_DATE}, {"ID": PROJECT}, {"ID": PROJECT, _COL: "Mon Oct 12 18:52:11 UTC 2026"}],
    ids=["no-such-project", "no-date", "unreadable-date"],
)
def test_history_without_a_creation_date_reads_every_page(xnat, row):
    xnat.on("GET", "/data/projects", lambda r: (200, _projects_listing(row)))
    old_page = [_request(i, at=0) for i in range(2 * PAGE, PAGE, -1)]
    xnat.pages("GET", "/xapi/dqr/import/history", [old_page, [_request(1, at=0)]])

    executed = get_executed_import_requests(PROJECT, {})

    assert len(xnat.requested("GET", "/xapi/dqr/import/history")) == 2
    assert len(executed) == PAGE + 1


@pytest.mark.parametrize("listing", [(503, "<html>Unavailable</html>"), (200, {"unexpected": True})])
def test_an_unreadable_project_listing_is_a_fetch_error(xnat, listing):
    xnat.on("GET", "/data/projects", lambda r: listing)

    with pytest.raises(XnatFetchError, match="project listing fetch failed"):
        get_executed_import_requests(PROJECT, {})
    assert xnat.requested("GET", "/xapi/dqr/import/history") == []


def test_the_creation_date_is_asked_of_xnat_for_this_project_only(xnat):
    xnat.pages("GET", "/xapi/dqr/import/history", [[]])

    get_executed_import_requests(PROJECT, {})

    assert _query(xnat.requested("GET", "/data/projects")[0]) == {
        "format": ["json"],
        "ID": [PROJECT],
        "columns": ["ID,xnat:projectData/meta/insert_date"],
    }


@pytest.mark.parametrize(
    ("inserted", "expected"),
    [
        (INSERT_DATE, HORIZON_MS),
        ("2026-10-12 18:52:11", HORIZON_MS - 266),
        ("2026-10-12T19:52:11.266+01:00", HORIZON_MS),
    ],
    ids=["xnat-form", "no-fraction", "with-offset"],
)
def test_the_horizon_is_a_day_before_the_creation_date(xnat, inserted, expected):
    xnat.on("GET", "/data/projects", lambda r: (200, _projects_listing({"ID": PROJECT, _COL: inserted})))

    assert import_requests._history_horizon_ms(PROJECT, {}) == expected


def test_a_request_with_no_timestamp_does_not_stop_the_history_read(xnat):
    first = [_request(i) for i in range(2 * PAGE, PAGE, -1)]
    first[-1] = {"id": PAGE + 1, "xnatProject": OTHER}
    xnat.pages("GET", "/xapi/dqr/import/history", [first, [_request(1)]])

    get_executed_import_requests(PROJECT, {})

    assert len(xnat.requested("GET", "/xapi/dqr/import/history")) == 2


# ===========================================================================
# Direct archive
# ===========================================================================


def _session(session_id: int, project: str = PROJECT, folder: str = "ACC", status: str = "ERROR", **extra: Any):
    return {"id": session_id, "project": project, "folderName": folder, "status": status, **extra}


def test_direct_archive_asks_xnat_to_filter_by_project(xnat):
    xnat.pages("POST", "/xapi/direct-archive", [[]])

    get_direct_archive_sessions(PROJECT, {})

    [request] = xnat.requested("POST", "/xapi/direct-archive")
    assert _body(request) == {
        "page": 1,
        "size": PAGE,
        "sort_col": "id",
        "sort_dir": "desc",
        "filters": {"project": {"backend": "hibernate", "operator": "eq", "value": PROJECT}},
    }


def test_direct_archive_reads_names_and_folder_names(xnat):
    xnat.pages(
        "POST",
        "/xapi/direct-archive",
        [[_session(2, folder="F2", name="N2", message="Session already exists"), _session(1, status="RECEIVING")]],
    )

    sessions = get_direct_archive_sessions(PROJECT, {})

    assert [(s.id, s.project, s.name, s.folder_name, s.status) for s in sessions] == [
        (2, PROJECT, "N2", "F2", "ERROR"),
        (1, PROJECT, None, "ACC", "RECEIVING"),
    ]
    assert all(isinstance(s, DirectArchiveSession) for s in sessions)


def test_direct_archive_ignores_another_projects_session_xnat_let_through(xnat):
    xnat.pages("POST", "/xapi/direct-archive", [[_session(2, OTHER), _session(1)]])

    assert [s.id for s in get_direct_archive_sessions(PROJECT, {})] == [1]


def test_direct_archive_reads_every_page(xnat):
    first = [_session(i) for i in range(2 * PAGE, PAGE, -1)]
    xnat.pages("POST", "/xapi/direct-archive", [first, [_session(1)]])

    assert len(get_direct_archive_sessions(PROJECT, {})) == PAGE + 1
    assert [_body(r)["page"] for r in xnat.requested("POST", "/xapi/direct-archive")] == [1, 2]


@pytest.mark.parametrize("status", [404, 500])
def test_an_unreadable_direct_archive_listing_is_a_fetch_error(xnat, status):
    xnat.on("POST", "/xapi/direct-archive", lambda r: (status, "There was an error in the request"))

    with pytest.raises(XnatFetchError, match=f"direct-archive session fetch failed: {status}"):
        get_direct_archive_sessions(PROJECT, {})


def test_a_direct_archive_transport_failure_is_a_fetch_error(xnat):
    def refuse(request):
        raise requests.Timeout("timed out")

    xnat.on("POST", "/xapi/direct-archive", refuse)

    with pytest.raises(XnatFetchError, match="timed out"):
        get_direct_archive_sessions(PROJECT, {})


def test_every_read_is_bounded_in_time(xnat):
    """An unbounded call to a wedged XNAT would hang the status check rather than fail it."""
    timeouts = []
    real_respond = xnat.respond

    def record_timeout(request, **kwargs):
        timeouts.append(kwargs.get("timeout"))
        return real_respond(request, **kwargs)

    xnat.pages("GET", "/xapi/dqr/import/queue", [[]])
    xnat.pages("GET", "/xapi/dqr/import/history", [[]])
    xnat.pages("POST", "/xapi/direct-archive", [[]])
    xnat.on("GET", f"/data/prearchive/projects/{PROJECT}", lambda r: (200, {"ResultSet": {"Result": []}}))
    xnat.on("POST", "/data/services/archive", lambda r: (303, ""))
    with patch.object(requests.Session, "send", side_effect=record_timeout):
        get_queued_import_requests(PROJECT, {})
        get_executed_import_requests(PROJECT, {})
        get_direct_archive_sessions(PROJECT, {})
        get_prearchive_sessions(PROJECT, {})
        archive_prearchive_session("/prearchive/projects/p/t/ACC", {})

    assert len(timeouts) == 6
    assert all(t == XNAT_REQUEST_TIMEOUT for t in timeouts)


# ===========================================================================
# Prearchive (FLIP#1511)
# ===========================================================================

PREARCHIVE = f"/data/prearchive/projects/{PROJECT}"


def _prearchived(name: str = "ACC", project: str = PROJECT, status: str = "ERROR") -> dict[str, Any]:
    """A prearchive session as xnat-web 1.10.0 lists it, here one whose direct-archive failed."""
    stamp = "20261016_095724314"
    return {
        "project": project,
        "timestamp": stamp,
        "folderName": name,
        "name": name,
        "status": status,
        "url": f"/prearchive/projects/{project}/{stamp}/{name}",
    }


def test_prearchive_lists_the_projects_sessions_with_their_urls(xnat):
    xnat.on(
        "GET",
        PREARCHIVE,
        lambda r: (
            200,
            {
                "ResultSet": {
                    "Result": [_prearchived("A1"), _prearchived("B1", OTHER), _prearchived("A2", status="READY")]
                }
            },
        ),
    )

    sessions = get_prearchive_sessions(PROJECT, {})

    [request] = xnat.requested("GET", PREARCHIVE)
    assert _query(request) == {"format": ["json"]}
    assert [(s.name, s.folder_name, s.status, s.url) for s in sessions] == [
        ("A1", "A1", "ERROR", f"/prearchive/projects/{PROJECT}/20261016_095724314/A1"),
        ("A2", "A2", "READY", f"/prearchive/projects/{PROJECT}/20261016_095724314/A2"),
    ]


def test_an_empty_prearchive_is_no_sessions(xnat):
    xnat.on("GET", PREARCHIVE, lambda r: (200, {"ResultSet": {"Result": []}}))

    assert get_prearchive_sessions(PROJECT, {}) == []


@pytest.mark.parametrize(
    "answer", [(500, "There was an error"), (200, {"unexpected": True})], ids=["500", "not-a-listing"]
)
def test_an_unreadable_prearchive_is_a_fetch_error(xnat, answer):
    xnat.on("GET", PREARCHIVE, lambda r: answer)

    with pytest.raises(XnatFetchError, match="prearchive session fetch failed"):
        get_prearchive_sessions(PROJECT, {})


@pytest.mark.parametrize("status", [200, 303])
def test_archiving_a_prearchive_session_appends_it_to_the_archive(xnat, status):
    src = _prearchived()["url"]
    xnat.on("POST", "/data/services/archive", lambda r: (status, ""))

    assert archive_prearchive_session(src, {}) is True

    [request] = xnat.requested("POST", "/data/services/archive")
    assert _query(request) == {"src": [src], "overwrite": ["append"]}


@pytest.mark.parametrize("status", [400, 409, 500])
def test_a_refused_archive_is_false(xnat, status):
    xnat.on("POST", "/data/services/archive", lambda r: (status, "Session already exists"))

    assert archive_prearchive_session(_prearchived()["url"], {}) is False


def test_an_archive_transport_failure_is_false_and_never_logs_the_session(xnat, caplog):
    """The prearchive URL names the session, i.e. the accession number, so it must not reach the log."""
    caplog.set_level(logging.DEBUG)
    src = _prearchived("ACC_SECRET")["url"]

    def refuse(request):
        raise requests.ConnectionError(f"failed to reach {request.url}")

    xnat.on("POST", "/data/services/archive", refuse)

    assert archive_prearchive_session(src, {}) is False
    assert "ConnectionError" in caplog.text
    assert "ACC_SECRET" not in caplog.text


def test_deleting_a_prearchive_session_deletes_it_by_its_url(xnat):
    src = _prearchived()["url"]
    xnat.on("DELETE", f"/data{src}", lambda r: (200, ""))

    assert delete_prearchive_session(src, {}) is True

    assert len(xnat.requested("DELETE", f"/data{src}")) == 1


@pytest.mark.parametrize("status", [403, 404, 500])
def test_a_refused_delete_is_false(xnat, status):
    src = _prearchived()["url"]
    xnat.on("DELETE", f"/data{src}", lambda r: (status, "Unable to modify session data"))

    assert delete_prearchive_session(src, {}) is False


def test_a_delete_transport_failure_is_false_and_never_logs_the_session(xnat, caplog):
    caplog.set_level(logging.DEBUG)
    src = _prearchived("ACC_SECRET")["url"]

    def refuse(request):
        raise requests.ConnectionError(f"failed to reach {request.url}")

    xnat.on("DELETE", f"/data{src}", refuse)

    assert delete_prearchive_session(src, {}) is False
    assert "ConnectionError" in caplog.text
    assert "ACC_SECRET" not in caplog.text
