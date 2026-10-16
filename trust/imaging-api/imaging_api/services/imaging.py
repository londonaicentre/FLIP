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


from typing import Any

import requests
from pydantic import TypeAdapter

from imaging_api.routers.schemas import (
    ImportStudyRequest,
    ImportStudyResponse,
    PacsStatus,
    Study,
    StudyQuery,
)
from imaging_api.services.projects import get_project
from imaging_api.utils.exceptions import NotFoundError, XnatFetchError
from imaging_api.utils.logger import logger
from imaging_api.utils.xnat_url import XNAT_REQUEST_TIMEOUT, xnat_url

# Bound every XNAT call: an unbounded request to a wedged XNAT would hang the import (and the
# health probe) rather than failing. The DQR data-path calls get a far larger bound because they
# proxy live DIMSE operations against the upstream PACS — a throttled or slow real PACS can hold
# a C-FIND well past what any metadata round-trip to XNAT itself would need.
XNAT_DQR_REQUEST_TIMEOUT = 300


def _describe_pacs(registration: dict[str, Any]) -> str:
    """Names a PACS registration for an error message: its AE title and XNAT id, never its host."""
    return f"'{registration.get('aeTitle')}' (id {registration.get('id')})"


def resolve_default_pacs_id(headers: dict[str, str]) -> int:
    """
    Resolves the id of the PACS XNAT flags as its default query/retrieve PACS.

    A query that names no PACS runs against this one, but DQR's import does not fall back to it, so an
    import (and an id-less ping) names it. Read on every call: the id is XNAT's to assign and changes
    when the PACS is re-registered (FLIP#993). There is no fallback id; a listing without exactly one
    default fails here, naming what XNAT reported.

    Args:
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        int: The id of the default query/retrieve PACS.

    Raises:
        imaging_api.utils.exceptions.XnatFetchError: If XNAT cannot be reached, answers other than 200,
            or returns a listing that is not a list of registrations, or the default one has no usable id.
        imaging_api.utils.exceptions.NotFoundError: If no queryable PACS is flagged as the default.
        Exception: If more than one is.
    """
    # queryable=true: DQR's import refuses any other PACS. DQR also leaves a disabled PACS out of this list.
    try:
        response = requests.get(
            xnat_url("xapi", "pacs", query={"queryable": "true"}),
            headers=headers,
            timeout=XNAT_REQUEST_TIMEOUT,
        )
    except requests.RequestException as e:
        raise XnatFetchError(f"Could not list the PACS registered in XNAT: {e}") from e
    if response.status_code != 200:
        raise XnatFetchError(f"Could not list the PACS registered in XNAT: HTTP {response.status_code}")
    try:
        registrations = response.json()
    except ValueError as e:
        raise XnatFetchError(f"XNAT's PACS listing is not JSON: {e}") from e
    if not isinstance(registrations, list) or not all(isinstance(p, dict) for p in registrations):
        raise XnatFetchError("XNAT's PACS listing is not a list of PACS registrations")

    defaults = [p for p in registrations if p.get("defaultQueryRetrievePacs") is True]
    if not defaults:
        found = ", ".join(_describe_pacs(p) for p in registrations) or "none"
        raise NotFoundError(
            f"XNAT has no PACS flagged as the default query/retrieve PACS (queryable PACS found: {found}), "
            "so DQR answers every query 404. Re-run XNAT's configure step (configure-xnat.sh; the xnat-init "
            "job on Kubernetes): it registers the trust's PACS, or restores the flag on it. A PACS disabled in "
            "DQR's admin page is not listed either, and the configure step does not re-enable it."
        )
    if len(defaults) > 1:
        raise Exception(
            f"XNAT flags {len(defaults)} PACS as the default query/retrieve PACS "
            f"({', '.join(_describe_pacs(p) for p in defaults)}); expected exactly one. "
            "A trust XNAT retrieves from one PACS: remove the extra registration in XNAT's admin UI."
        )

    chosen = defaults[0]
    try:
        pacs_id = int(chosen["id"])
    except (KeyError, TypeError, ValueError) as e:
        raise XnatFetchError(
            f"XNAT's default query/retrieve PACS {_describe_pacs(chosen)} has no usable id ({e!r})"
        ) from e

    # Debug, not info: trust-api's health probe resolves through here every 30 seconds.
    logger.debug(f"Resolved the default query/retrieve PACS {_describe_pacs(chosen)}")
    return pacs_id


def ping_registered_pacs(headers: dict[str, str]) -> PacsStatus:
    """
    Pings the trust's PACS: the default query/retrieve PACS, resolved from XNAT on this call.

    trust-api's health probe calls this through ``GET /imaging/ping_pacs``, so it must not need to know
    the id XNAT assigned. Resolving per call means a PACS re-registered under a new id while
    imaging-api stays up is pinged at its new id on the next probe — there is no stale id to drop.

    Args:
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        PacsStatus: Status of the PACS system.

    Raises:
        imaging_api.utils.exceptions.NotFoundError: If XNAT has no default query/retrieve PACS, or no
            longer has the resolved one when it is pinged.
        Exception: If the PACS cannot be resolved (see ``resolve_default_pacs_id``) or the ping fails.
    """
    return ping_pacs(resolve_default_pacs_id(headers), headers)


def ping_pacs(pacs_id: int, headers: dict[str, str]) -> PacsStatus:
    """
    Pings the imaging provider (PACS) to check if it is reachable.

    Args:
        pacs_id (int): PACS ID to ping.
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        PacsStatus: Status of the PACS system.

    Raises:
        imaging_api.utils.exceptions.NotFoundError: If the PACS with the given ID is not found.
        Exception: If there is an error during the ping request.
    """
    response = requests.get(
        xnat_url("xapi", "pacs", str(pacs_id), "status"),
        headers=headers,
        timeout=XNAT_REQUEST_TIMEOUT,
    )
    if response.status_code == 200:
        return PacsStatus(**response.json())
    elif response.status_code == 404:
        raise NotFoundError(f"PACS with ID '{pacs_id}' not found.")
    else:
        raise Exception(f"Failed to ping PACS: {response.text}")


def check_pacs(headers: dict[str, str], pacs_id: int) -> None:
    """
    Checks if the PACS system is reachable by pinging it.

    Args:
        headers (dict[str, str]): XNAT authentication headers.
        pacs_id (int): PACS ID to check.

    Returns:
        None

    Raises:
        imaging_api.utils.exceptions.NotFoundError: If the PACS with the given ID is not found.
        Exception: If there is an error during the ping request or if the PACS is not reachable or is disabled.
    """
    try:
        pacs_status = ping_pacs(pacs_id, headers)
    except NotFoundError:
        raise
    except Exception:
        raise Exception(f"Failed to ping PACS with ID '{pacs_id}'.")
    if not pacs_status.successful:
        raise Exception(f"PACS with ID '{pacs_id}' is not reachable.")
    if not pacs_status.enabled:
        raise Exception(f"PACS with ID '{pacs_id}' is disabled.")
    logger.info(f"PACS with ID '{pacs_id}' is reachable.")


def query_by_accession_number(accession_number: str, headers: dict[str, str]) -> list[Study]:
    """
    Queries the imaging provider (PACS) to retrieve a list of studies associated with the provided accession number.

    The query names no PACS, so DQR runs it against its default query/retrieve PACS — the one
    ``configure-xnat.sh`` keeps registered and flags.

    Args:
        accession_number (str): The accession number to query.
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        list[Study]: A list of Study objects that match the accession number.

    Raises:
        Exception: If there is an error during the query request.
    """
    study_query = StudyQuery(accessionNumber=accession_number)

    response = requests.post(
        xnat_url("xapi", "dqr", "query", "studies"),
        headers=headers,
        json=study_query.model_dump(by_alias=True),
        timeout=XNAT_DQR_REQUEST_TIMEOUT,
    )
    # Status and reason only. The DQR query response body is a study list carrying patient id, name,
    # sex, referring physician, accession number and study date (see routers/schemas.py), so logging
    # it verbatim writes patient identifiers into the trust's container logs — mock data until this
    # XNAT is pointed at a real hospital PACS, real patients afterwards. Dev trusts run at
    # LOG_LEVEL=DEBUG, so this line is emitted today. The study count is logged after parsing below,
    # which is the part that is actually useful when debugging a retrieval.
    logger.debug(f"Query response: {response.status_code} - {response.reason}")

    if response.status_code == 200:
        logger.info("Successfully queried PACS via DQR")
    elif response.status_code == 204:
        logger.warning("No studies found via DQR")
        return []
    elif response.status_code == 401:
        raise Exception("Unauthorized to query PACS via DQR")
    else:
        raise Exception(f"Failed to query PACS via DQR (HTTP {response.status_code})")

    # Convert raw API response to a list of Study models
    response_data = response.json()
    studies = [Study(**study) for study in response_data]

    logger.info(f"Found {len(studies)} studies via DQR")
    return studies


def queue_image_import_request(
    import_request: ImportStudyRequest, headers: dict[str, str]
) -> list[ImportStudyResponse]:
    """
    Queues an image import request via DQR for the provided XNAT project ID.
    Handles duplicate studies by StudyInstanceUID and checks if all studies were successfully queued.

    Note that trying to retrieve a study which is already contained in the project will still successfully
    queue the import, but will result in an error on XNAT.

    Args:
        import_request (ImportStudyRequest): The import request containing project ID and study details.
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        list[ImportStudyResponse]: A list of ImportStudyResponse objects representing the queued import requests.

    Raises:
        imaging_api.utils.exceptions.NotFoundError: If the project with the given ID is not found, if XNAT has
        no default query/retrieve PACS, or if no studies are found on PACS.
        Exception: If there is an error during the import request, or XNAT's default query/retrieve PACS
        cannot be resolved.
    """
    logger.info(
        f"Queuing image import request for project '{import_request.project_id}' "
        f"with {len(import_request.studies)} studies"
    )

    # Calling DQR API with a non-existent project works, which is pointless,
    # because we load the PACS but don't actually retrieve anything.
    # Check if the project exists before queuing the import request.
    # Check if project exists
    get_project(import_request.project_id, headers=headers)

    # Retrieve from the PACS the C-FINDs queried. They named none, so DQR used its default
    # query/retrieve PACS; DQR's import does not fall back to that default, so name it here, read from
    # XNAT once for this request. Any pacsId the caller sent is overwritten.
    import_request.pacs_id = resolve_default_pacs_id(headers)

    # Check PACS
    check_pacs(headers=headers, pacs_id=import_request.pacs_id)

    # Send import request to DQR
    response = requests.post(
        xnat_url("xapi", "dqr", "import"),
        headers=headers,
        json=import_request.model_dump(by_alias=True),
        timeout=XNAT_DQR_REQUEST_TIMEOUT,
    )

    if response.status_code == 200:
        import_response: list[ImportStudyResponse] = TypeAdapter(list[ImportStudyResponse]).validate_json(response.text)
        logger.info(f"Import response returned {len(import_response)} studies.")
    elif response.status_code == 404:
        raise NotFoundError(f"Not found error for project '{import_request.project_id}'")
    else:
        raise Exception(f"Failed to queue image import via DQR for project '{import_request.project_id}'")

    # If none of the studies were found on PACS, import_response will be an empty list
    if not import_response:
        raise NotFoundError("No studies found on PACS with the study instance UID(s) provided")

    # Check number of studies provided with number of studies returned in the response
    if len(import_request.studies) != len(import_response):
        raise ValueError(
            f"""Some studies not found on PACS: Number of studies provided ({len(import_request.studies)})
            does not match number of studies returned in the response ({len(import_response)})"""
        )

    # Check if all retrievals were successfully queued
    if all(item.status == "QUEUED" for item in import_response):
        logger.info("Successfully queued all studies for retrieval via DQR for project '%s'", import_request.project_id)
    else:
        logger.error("One or more studies failed to queue via DQR for project '%s'", import_request.project_id)

    return import_response
