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

"""Reads DQR's import queue and history, and XNAT's direct-archive sessions, through XNAT's REST API.

``get_import_status`` classifies a cohort's accessions by these records, and
``delete_queued_import_requests`` clears a deleted project's queue with them. imaging-api used to read
them from XNAT's own Postgres (the ``xhbm_*`` tables), holding XNAT's application database credential
to do it, a credential that reads and writes every XNAT table (FLIP#1456 B1). XNAT serves the same rows:

- ``GET /xapi/dqr/import/queue`` and ``GET /xapi/dqr/import/history`` (DQR 3.0.0) page through the
  queued and the executed requests, ordered by id. Neither takes a project filter, so rows are kept
  here by ``xnatProject``. ``all=true`` returns every user's requests, as the table read did; DQR allows
  it only for a user with all-data access, which the service account has as a site Administrator
  (``configure-xnat.sh``). ``all=false`` would return the service account's own requests only.
- ``POST /xapi/direct-archive`` (xnat-web 1.10.0) pages through the direct-archive sessions, with a
  ``project`` filter that XNAT ANDs with the projects the caller can read.

A failed read raises ``XnatFetchError``, never ``NotFoundError``: callers answer it with a 500, and the
hub reads a 404 from imaging-api as "the XNAT project is missing" (FLIP#1022).
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import requests
from pydantic import BaseModel, Field

from imaging_api.utils.exceptions import XnatFetchError
from imaging_api.utils.logger import logger
from imaging_api.utils.xnat_url import xnat_url

# The bound services/imaging.py puts on an XNAT metadata call. Not imported from there, because
# services/imaging.py imports services/projects.py, which imports this module.
_XNAT_REQUEST_TIMEOUT = 30

# Rows per page. A DQR history row is about 600 bytes of JSON, so a full page is about 0.6 MB.
_PAGE_SIZE = 1000

# How far before the project's recorded creation the history read goes. XNAT reports insert_date as
# the server's wall-clock time with no zone, and it is read here as UTC. No zone is more than 14
# hours from UTC, so a day earlier is before the project existed whatever zone the server runs in.
_HISTORY_MARGIN = timedelta(days=1)
_INSERT_DATE_COLUMN = "xnat:projectData/meta/insert_date"

# The epoch-millisecond fields a DQR request carries. The latest of them (``timestamp`` is the last
# update) is no earlier than the row was written.
_REQUEST_TIME_FIELDS = ("queuedTime", "created", "executedTime", "timestamp")


class PacsRequest(BaseModel):
    """A DQR import request, queued or executed, with the fields imaging-api reads.

    DQR leaves null fields out of the JSON, so everything but ``id`` is optional.
    """

    id: int
    xnat_project: str | None = Field(default=None, alias="xnatProject")
    accession_number: str | None = Field(default=None, alias="accessionNumber")
    status: str | None = None


class DirectArchiveSession(BaseModel):
    """A direct-archive session, as ``POST /xapi/direct-archive`` lists it (XNAT's ``SessionData``)."""

    id: int | None = None
    project: str | None = None
    # The session label, which for a DQR import is the accession number: the key get_import_status
    # maps an archive ERROR back to a cohort accession by. It can be null, and folder_name carries the
    # same label, so get_import_status falls back to it.
    name: str | None = None
    folder_name: str | None = Field(default=None, alias="folderName")
    status: str | None = None


def _records(response: requests.Response, label: str) -> list[dict[str, Any]]:
    """Returns the records in an XNAT listing response.

    Args:
        response (requests.Response): XNAT's response.
        label (str): What was being listed, for the error message.

    Returns:
        list[dict[str, Any]]: The records.

    Raises:
        XnatFetchError: If XNAT answered other than 200 or the body is not a JSON list of records.
    """
    if response.status_code != 200:
        raise XnatFetchError(f"Error: XNAT {label} fetch failed: {response.status_code} - {response.text[:200]}")
    try:
        body = response.json()
    except ValueError as e:
        raise XnatFetchError(f"Error: XNAT {label} fetch failed: the response is not JSON") from e
    if not isinstance(body, list) or not all(isinstance(record, dict) for record in body):
        raise XnatFetchError(f"Error: XNAT {label} fetch failed: the response is not a list of records")
    return body


def _all_pages(
    fetch_page: Callable[[int], list[dict[str, Any]]],
    stop_after: Callable[[dict[str, Any]], bool] | None = None,
) -> list[dict[str, Any]]:
    """Reads a paginated listing page by page, keeping each record once.

    Stops at a short page, at a page that brings no record not already read (a server that ignores
    the page number), or when ``stop_after`` says the last record of a page is as far as the
    caller needs to read. Records arriving while the pages are read can push a record onto the next
    page as well, so records are kept once by id.

    Args:
        fetch_page (Callable[[int], list[dict[str, Any]]]): Returns one page, numbered from 1.
        stop_after (Callable[[dict[str, Any]], bool] | None): Called with the last record of each
            full page; True stops the read there.

    Returns:
        list[dict[str, Any]]: The records, in listing order.
    """
    records: list[dict[str, Any]] = []
    seen: set[Any] = set()
    page_number = 1
    while True:
        page = fetch_page(page_number)
        new = [record for record in page if record.get("id") is None or record["id"] not in seen]
        seen.update(record.get("id") for record in new)
        records.extend(new)
        if len(page) < _PAGE_SIZE or not new or (stop_after is not None and stop_after(page[-1])):
            return records
        page_number += 1


def _get_dqr_page(listing: str, page_number: int, headers: dict[str, str]) -> list[dict[str, Any]]:
    """Fetches one page of DQR's import queue or history, newest first.

    Args:
        listing (str): ``queue`` or ``history``.
        page_number (int): The page, numbered from 1.
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        list[dict[str, Any]]: The page's requests.

    Raises:
        XnatFetchError: If the request fails or XNAT answers with anything but a list of requests.
    """
    label = f"DQR import {listing}"
    url = xnat_url(
        "xapi",
        "dqr",
        "import",
        listing,
        query={"all": "true", "sort": "desc", "page": str(page_number), "pageSize": str(_PAGE_SIZE)},
    )
    try:
        response = requests.get(url, headers=headers, timeout=_XNAT_REQUEST_TIMEOUT)
    except requests.RequestException as e:
        raise XnatFetchError(f"Error: XNAT {label} fetch failed: {e}") from e
    return _records(response, label)


def _history_horizon_ms(project_id: str, headers: dict[str, str]) -> int | None:
    """Returns the time before which no DQR request can belong to the project.

    The history has no project filter and only grows, so reading all of it on every status check
    costs more the longer the trust runs. A project's requests are all queued after XNAT created
    the project, so the history, read newest first, can stop a margin before that.

    Args:
        project_id (str): The XNAT project ID.
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        int | None: Epoch milliseconds, or None when XNAT does not say when the project was created,
        in which case the whole history is read.
    """
    query = {"format": "json", "ID": project_id, "columns": f"ID,{_INSERT_DATE_COLUMN}"}
    try:
        response = requests.get(
            xnat_url("data", "projects", query=query), headers=headers, timeout=_XNAT_REQUEST_TIMEOUT
        )
        listing = response.json()["ResultSet"]["Result"] if response.status_code == 200 else []
    except (requests.RequestException, ValueError, KeyError, TypeError):
        listing = []

    # XNAT reads ID as a pattern (a * or % in it is a wildcard), so only an exact match counts. It
    # names the column in lower case.
    rows = listing if isinstance(listing, list) else []
    matches = [row for row in rows if isinstance(row, dict) and row.get("ID") == project_id]
    inserted = matches[0].get(_INSERT_DATE_COLUMN.lower()) if matches else None
    try:
        created = datetime.fromisoformat(inserted) if isinstance(inserted, str) else None
    except ValueError:
        created = None
    if created is None:
        logger.warning(f"XNAT did not say when project {project_id} was created; reading the whole DQR import history")
        return None
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return int((created - _HISTORY_MARGIN).timestamp() * 1000)


def _latest_time_ms(request: dict[str, Any]) -> int | None:
    """Returns the latest of a DQR request's timestamps, or None when it carries none.

    Args:
        request (dict[str, Any]): A DQR request as XNAT returns it.

    Returns:
        int | None: Epoch milliseconds.
    """
    times = [
        value
        for value in (request.get(field) for field in _REQUEST_TIME_FIELDS)
        if isinstance(value, int) and not isinstance(value, bool)
    ]
    return max(times) if times else None


def get_queued_import_requests(project_id: str, headers: dict[str, str]) -> list[PacsRequest]:
    """Lists the project's requests in DQR's import queue (``GET /xapi/dqr/import/queue``).

    Args:
        project_id (str): The XNAT project ID.
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        list[PacsRequest]: The project's queued requests, newest first.

    Raises:
        XnatFetchError: If the queue cannot be read.
    """
    queue = _all_pages(lambda page_number: _get_dqr_page("queue", page_number, headers))
    project_requests = [PacsRequest.model_validate(r) for r in queue if r.get("xnatProject") == project_id]
    logger.info(f"Found {len(project_requests)} queued PACS requests for project {project_id}")
    return project_requests


def get_executed_import_requests(project_id: str, headers: dict[str, str]) -> list[PacsRequest]:
    """Lists the project's executed requests in DQR's import history (``GET /xapi/dqr/import/history``).

    The history is listed by id, newest first, so each row was written before every row above it. The
    read stops after the first full page whose last row has every timestamp before the horizon, a day
    before XNAT created the project (see ``_history_horizon_ms``). Every row below that one was
    written, and so queued, before then too, so none of them can be the project's.

    Args:
        project_id (str): The XNAT project ID.
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        list[PacsRequest]: The project's executed requests, newest first.

    Raises:
        XnatFetchError: If the history cannot be read.
    """
    horizon_ms = _history_horizon_ms(project_id, headers)

    def is_older_than_project(request: dict[str, Any]) -> bool:
        latest = _latest_time_ms(request)
        return horizon_ms is not None and latest is not None and latest < horizon_ms

    history = _all_pages(lambda page_number: _get_dqr_page("history", page_number, headers), is_older_than_project)
    project_requests = [PacsRequest.model_validate(r) for r in history if r.get("xnatProject") == project_id]
    logger.info(
        f"Found {len(project_requests)} executed PACS requests for project {project_id} "
        f"({len(history)} history rows read)"
    )
    return project_requests


def get_direct_archive_sessions(project_id: str, headers: dict[str, str]) -> list[DirectArchiveSession]:
    """Lists the project's direct-archive sessions (``POST /xapi/direct-archive``).

    A session that archives successfully is deleted, so these are the ones still receiving, building
    or failed.

    Args:
        project_id (str): The XNAT project ID.
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        list[DirectArchiveSession]: The project's direct-archive sessions.

    Raises:
        XnatFetchError: If the sessions cannot be read.
    """
    label = "direct-archive session"

    def fetch_page(page_number: int) -> list[dict[str, Any]]:
        body = {
            "page": page_number,
            "size": _PAGE_SIZE,
            "sort_col": "id",
            "sort_dir": "desc",
            "filters": {"project": {"backend": "hibernate", "operator": "eq", "value": project_id}},
        }
        try:
            response = requests.post(
                xnat_url("xapi", "direct-archive"), headers=headers, json=body, timeout=_XNAT_REQUEST_TIMEOUT
            )
        except requests.RequestException as e:
            raise XnatFetchError(f"Error: XNAT {label} fetch failed: {e}") from e
        return _records(response, label)

    # XNAT has already filtered by project. Checking again means a filter it stopped honouring would
    # not attribute another project's sessions to this one.
    sessions = [
        DirectArchiveSession.model_validate(s) for s in _all_pages(fetch_page) if s.get("project") == project_id
    ]
    logger.info(f"Found {len(sessions)} direct archive sessions for project {project_id}")
    return sessions
