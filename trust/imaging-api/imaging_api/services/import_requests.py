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

"""Reads DQR's import queue and history, and XNAT's direct-archive and prearchive sessions, through XNAT's REST API.

- ``GET /xapi/dqr/import/queue`` and ``GET /xapi/dqr/import/history`` (DQR 3.0.0) page through the
  queued and the executed requests, ordered by id. Neither takes a project filter, so each page is
  filtered here by ``xnatProject``. ``all=true`` returns every user's requests; DQR allows it only to a
  user with all-data access, which the service account has as a site Administrator.
- ``POST /xapi/direct-archive`` (xnat-web 1.10.0) pages through the direct-archive sessions, with a
  ``project`` filter that XNAT ANDs with the projects the caller can read.
- ``GET /data/prearchive/projects/{project}`` lists the project's prearchive sessions. A session whose
  direct-archive failed lands there with status ERROR, and ``POST /data/services/archive`` re-archives
  it (FLIP#1511).

A failed read raises ``XnatFetchError``, never ``NotFoundError``: callers answer it with a 500, and the
hub reads a 404 from imaging-api as "the XNAT project is missing" (FLIP#1022).
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

import requests
from pydantic import BaseModel, Field

from imaging_api.utils.exceptions import XnatFetchError
from imaging_api.utils.logger import logger
from imaging_api.utils.xnat_url import XNAT_REQUEST_TIMEOUT, xnat_url

_PAGE_SIZE = 1000

# XNAT reports insert_date as the server's wall-clock time with no zone, and it is read here as UTC.
# No zone is a day from UTC, so a day earlier is before the project existed wherever XNAT runs.
_HISTORY_MARGIN = timedelta(days=1)
_INSERT_DATE_COLUMN = "xnat:projectData/meta/insert_date"


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


class PrearchiveSession(BaseModel):
    """A prearchive session, as ``GET /data/prearchive/projects/{project}`` lists it.

    When a direct-archive fails, XNAT 1.10.0 moves the session here with status ERROR and deletes its
    direct-archive session, so this listing is the only place the failure is still visible (FLIP#1511).
    """

    project: str | None = None
    # The session label (the accession number for a DQR import), as on a direct-archive session.
    name: str | None = None
    folder_name: str | None = Field(default=None, alias="folderName")
    status: str | None = None
    # The session's prearchive path, which XNAT's archive service takes as ``src``.
    url: str | None = None


def _send(method: str, url: str, headers: dict[str, str], label: str, **kwargs: Any) -> requests.Response:
    """Sends one XNAT request.

    Raises:
        XnatFetchError: If the request fails in transport.
    """
    try:
        return requests.request(method, url, headers=headers, timeout=XNAT_REQUEST_TIMEOUT, **kwargs)
    except requests.RequestException as e:
        raise XnatFetchError(f"Error: XNAT {label} fetch failed: {e}") from e


def _json(response: requests.Response, label: str) -> Any:
    """Returns the JSON body of a 200 response.

    Raises:
        XnatFetchError: If XNAT answered other than 200, or not with JSON.
    """
    if response.status_code != 200:
        raise XnatFetchError(f"Error: XNAT {label} fetch failed: {response.status_code} - {response.text[:200]}")
    try:
        return response.json()
    except ValueError as e:
        raise XnatFetchError(f"Error: XNAT {label} fetch failed: the response is not JSON") from e


def _records(response: requests.Response, label: str) -> list[dict[str, Any]]:
    """Returns the records of an XNAT listing response.

    Raises:
        XnatFetchError: If the response is not a 200 with a JSON list of records.
    """
    body = _json(response, label)
    if not isinstance(body, list) or not all(isinstance(record, dict) for record in body):
        raise XnatFetchError(f"Error: XNAT {label} fetch failed: the response is not a list of records")
    return body


def _project_records(
    fetch_page: Callable[[int], list[dict[str, Any]]],
    belongs: Callable[[dict[str, Any]], bool],
    label: str,
    stop_after: Callable[[dict[str, Any]], bool] | None = None,
) -> list[dict[str, Any]]:
    """Reads a paginated listing page by page and keeps the project's records.

    Each page is filtered as it arrives, so only the project's records and the ids read so far are
    held. A record written between two page reads pushes the rest down one, repeating a record on the
    next page, so records are kept once by id. The read ends at a short page, or after a full page
    whose last record ``stop_after`` says is as far as the caller needs.

    Args:
        fetch_page (Callable[[int], list[dict[str, Any]]]): Returns one page, numbered from 1.
        belongs (Callable[[dict[str, Any]], bool]): Whether a record is the project's.
        label (str): What is being listed, for the error message.
        stop_after (Callable[[dict[str, Any]], bool] | None): Called with the last record of a full page.

    Returns:
        list[dict[str, Any]]: The project's records, in listing order.

    Raises:
        XnatFetchError: If a full page holds nothing not already read. XNAT ignored the page number,
            and stopping there would drop the rest of the listing, reporting its accessions as never
            queued.
    """
    kept: list[dict[str, Any]] = []
    seen: set[Any] = set()
    page_number = 1
    while True:
        page = fetch_page(page_number)
        new = [record for record in page if record.get("id") is None or record["id"] not in seen]
        seen.update(record.get("id") for record in new)
        kept.extend(record for record in new if belongs(record))
        if len(page) < _PAGE_SIZE:
            return kept
        if not new:
            raise XnatFetchError(f"Error: XNAT {label} fetch failed: page {page_number} repeats the pages before it")
        if stop_after is not None and stop_after(page[-1]):
            return kept
        page_number += 1


def _dqr_page(listing: str, page_number: int, headers: dict[str, str]) -> list[dict[str, Any]]:
    """Fetches one page of DQR's import queue or history, newest first.

    Raises:
        XnatFetchError: If the page cannot be read.
    """
    label = f"DQR import {listing}"
    query = {"all": "true", "sort": "desc", "page": str(page_number), "pageSize": str(_PAGE_SIZE)}
    response = _send("GET", xnat_url("xapi", "dqr", "import", listing, query=query), headers, label)
    if response.status_code == 403:
        raise XnatFetchError(f"Error: XNAT {label} fetch failed: 403 - the service account lacks all-data access")
    return _records(response, label)


def _history_horizon_ms(project_id: str, headers: dict[str, str]) -> int | None:
    """Returns the time before which no DQR request can belong to the project.

    A project's requests are all queued after XNAT created it, so the history, read newest first,
    can stop a day before that.

    Args:
        project_id (str): The XNAT project ID.
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        int | None: Epoch milliseconds, or None when XNAT lists no such project or gives it no
        creation date, in which case the whole history is read.

    Raises:
        XnatFetchError: If the project listing cannot be read.
    """
    label = "project listing"
    query = {"format": "json", "ID": project_id, "columns": f"ID,{_INSERT_DATE_COLUMN}"}
    body = _json(_send("GET", xnat_url("data", "projects", query=query), headers, label), label)
    try:
        rows = body["ResultSet"]["Result"]
    except (KeyError, TypeError) as e:
        raise XnatFetchError(f"Error: XNAT {label} fetch failed: the response is not a project listing") from e

    # XNAT reads ID as a pattern (a * or % in it is a wildcard), so only an exact match counts. It
    # names the column in lower case.
    match = next((row for row in rows if row.get("ID") == project_id), None)
    inserted = match.get(_INSERT_DATE_COLUMN.lower()) if match else None
    try:
        created = datetime.fromisoformat(inserted) if isinstance(inserted, str) else None
    except ValueError:
        created = None
    if created is None:
        logger.warning(f"XNAT gives no creation date for project {project_id}; reading the whole DQR import history")
        return None
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return int((created - _HISTORY_MARGIN).timestamp() * 1000)


def _updated_before(horizon_ms: int, request: dict[str, Any]) -> bool:
    """Whether a DQR request was last updated (``timestamp``, epoch ms) before ``horizon_ms``."""
    return isinstance(t := request.get("timestamp"), int) and t < horizon_ms


def _dqr_requests(
    listing: str,
    project_id: str,
    headers: dict[str, str],
    stop_after: Callable[[dict[str, Any]], bool] | None = None,
) -> list[PacsRequest]:
    """Lists the project's requests in DQR's import ``listing`` (``queue`` or ``history``), newest first."""
    records = _project_records(
        lambda page_number: _dqr_page(listing, page_number, headers),
        lambda request: request.get("xnatProject") == project_id,
        f"DQR import {listing}",
        stop_after,
    )
    return [PacsRequest.model_validate(request) for request in records]


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
    queue = _dqr_requests("queue", project_id, headers)
    logger.info(f"Found {len(queue)} queued PACS requests for project {project_id}")
    return queue


def get_executed_import_requests(project_id: str, headers: dict[str, str]) -> list[PacsRequest]:
    """Lists the project's executed requests in DQR's import history (``GET /xapi/dqr/import/history``).

    The history is listed by id, newest first, so each row was written before every row above it. The
    read stops after the first full page whose last row was last updated (``timestamp``) before the
    horizon, a day before XNAT created the project (see ``_history_horizon_ms``). Every row below
    that one was written, and so queued, before then too, so none of them can be the project's.

    Args:
        project_id (str): The XNAT project ID.
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        list[PacsRequest]: The project's executed requests, newest first.

    Raises:
        XnatFetchError: If the history, or the project's creation date, cannot be read.
    """
    horizon_ms = _history_horizon_ms(project_id, headers)
    history = _dqr_requests(
        "history", project_id, headers, None if horizon_ms is None else partial(_updated_before, horizon_ms)
    )
    logger.info(f"Found {len(history)} executed PACS requests for project {project_id}")
    return history


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
    body = {
        "size": _PAGE_SIZE,
        "sort_col": "id",
        "sort_dir": "desc",
        "filters": {"project": {"backend": "hibernate", "operator": "eq", "value": project_id}},
    }

    def fetch_page(page_number: int) -> list[dict[str, Any]]:
        response = _send("POST", xnat_url("xapi", "direct-archive"), headers, label, json={**body, "page": page_number})
        return _records(response, label)

    # XNAT has already filtered by project. Checking again means a filter it stopped honouring would
    # not attribute another project's sessions to this one.
    sessions = _project_records(fetch_page, lambda session: session.get("project") == project_id, label)
    logger.info(f"Found {len(sessions)} direct archive sessions for project {project_id}")
    return [DirectArchiveSession.model_validate(session) for session in sessions]


def get_prearchive_sessions(project_id: str, headers: dict[str, str]) -> list[PrearchiveSession]:
    """Lists the project's prearchive sessions (``GET /data/prearchive/projects/{project}``).

    Args:
        project_id (str): The XNAT project ID.
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        list[PrearchiveSession]: The project's prearchive sessions.

    Raises:
        XnatFetchError: If the sessions cannot be read.
    """
    label = "prearchive session"
    url = xnat_url("data", "prearchive", "projects", project_id, query={"format": "json"})
    body = _json(_send("GET", url, headers, label), label)
    try:
        rows = body["ResultSet"]["Result"]
    except (KeyError, TypeError) as e:
        raise XnatFetchError(f"Error: XNAT {label} fetch failed: the response is not a prearchive listing") from e

    # As with the direct-archive filter, a listing that let another project's session through must
    # not attribute it to this one.
    sessions = [PrearchiveSession.model_validate(row) for row in rows if row.get("project") == project_id]
    logger.info(f"Found {len(sessions)} prearchive sessions for project {project_id}")
    return sessions


def archive_prearchive_session(src: str, headers: dict[str, str]) -> bool:
    """Archives a prearchive session (``POST /data/services/archive``), appending to any archived session.

    Neither the URL nor an error carrying it is logged: ``src`` names the session, i.e. the accession.

    Args:
        src (str): The session's prearchive path, as ``PrearchiveSession.url`` gives it.
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        bool: True when XNAT archived it (a 2xx, or the 303 to the archived session it answers with).
    """
    url = xnat_url("data", "services", "archive", query={"src": src, "overwrite": "append"})
    try:
        response = requests.request("POST", url, headers=headers, timeout=XNAT_REQUEST_TIMEOUT, allow_redirects=False)
    except requests.RequestException as e:
        logger.warning(f"XNAT archive request failed in transport ({type(e).__name__})")
        return False
    if response.status_code == 303 or 200 <= response.status_code < 300:
        return True
    logger.warning(f"XNAT archive request failed: {response.status_code}")
    return False


def delete_prearchive_session(src: str, headers: dict[str, str]) -> bool:
    """Deletes a prearchive session (``DELETE /data/prearchive/projects/{project}/{timestamp}/{session}``).

    XNAT also clears the study's routing, so a study pulled from the PACS again lands in a fresh session.
    Neither the URL nor an error carrying it is logged: ``src`` names the session, i.e. the accession.

    Args:
        src (str): The session's prearchive path, as ``PrearchiveSession.url`` gives it.
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        bool: True when XNAT deleted it.
    """
    url = xnat_url("data", *src.strip("/").split("/"))
    try:
        response = requests.request("DELETE", url, headers=headers, timeout=XNAT_REQUEST_TIMEOUT)
    except requests.RequestException as e:
        logger.warning(f"XNAT prearchive delete failed in transport ({type(e).__name__})")
        return False
    if 200 <= response.status_code < 300:
        return True
    logger.warning(f"XNAT prearchive delete failed: {response.status_code}")
    return False
