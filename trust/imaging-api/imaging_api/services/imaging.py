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


import requests
from pydantic import TypeAdapter

from imaging_api.config import get_settings
from imaging_api.routers.schemas import (
    ImportStudyRequest,
    ImportStudyResponse,
    PacsStatus,
    Study,
    StudyQuery,
)
from imaging_api.services.projects import get_project
from imaging_api.utils.exceptions import NotFoundError
from imaging_api.utils.logger import logger

PACS_ID = get_settings().PACS_ID
XNAT_URL = get_settings().XNAT_URL
# Bound the PACS-id lookup: it sits on the retrieval path, and an unbounded request to a wedged
# XNAT would hang the import rather than falling back.
XNAT_REQUEST_TIMEOUT = 30

# Cache for resolve_pacs_id(). XNAT assigns a PACS its id at registration time, so the id is fixed
# for the life of that registration and re-resolving on every query would add an XNAT round-trip per
# accession number. Note the cache outlives the registration: if the PACS is re-registered while
# imaging-api stays up — configure-xnat.sh deletes and recreates it when the AE title changes — the
# cached id points at a deleted entry, so it is cleared when XNAT reports the PACS missing.
_resolved_pacs_id: int | None = None


def resolve_pacs_id(headers: dict[str, str]) -> int:
    """
    Resolves the id of the PACS registered in XNAT.

    A trust XNAT retrieves from exactly one PACS, and ``configure-xnat.sh`` enforces that by removing
    any other registration. The id itself cannot be assumed: XNAT numbers registrations in creation
    order, so an XNAT that carried the mocked Orthanc before the real PACS was configured does not
    have it at id 1 (FLIP#993).

    Falls back to the configured ``PACS_ID`` when XNAT cannot be reached or reports no PACS, so a
    transient XNAT failure degrades to the previous behaviour rather than failing the import.

    Args:
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        int: The id of the registered PACS, or the configured ``PACS_ID`` fallback.
    """
    global _resolved_pacs_id
    if _resolved_pacs_id is not None:
        return _resolved_pacs_id

    try:
        response = requests.get(f"{XNAT_URL}/xapi/pacs", headers=headers, timeout=XNAT_REQUEST_TIMEOUT)
        response.raise_for_status()
        registrations = response.json()
    except Exception as e:
        # Deliberately not cached: a transient failure must not pin the fallback for the life of the
        # process. Logged at error because the fallback is a guess — on an XNAT that carried the
        # mocked Orthanc before the real PACS, id 1 is the known-wrong answer, and the symptom is
        # "no study found" for every accession, which reads as a data problem rather than a
        # misconfiguration (FLIP#993).
        logger.error(
            f"Could not resolve the PACS id from XNAT ({e}); falling back to id {PACS_ID}. "
            f"Retrieval will target that id, which may not be the configured PACS."
        )
        return PACS_ID

    if not isinstance(registrations, list) or not registrations:
        logger.error(
            f"XNAT reports no registered PACS; falling back to id {PACS_ID}. "
            f"Retrieval will target that id, which may not be the configured PACS."
        )
        return PACS_ID

    # configure-xnat.sh sets defaultQueryRetrievePacs on the one it owns and removes any other, so
    # prefer that flag over list order — an extra registration added through the XNAT UI would
    # otherwise be picked purely because XNAT happened to list it first.
    default_qr = [p for p in registrations if p.get("defaultQueryRetrievePacs")]
    chosen = (default_qr or registrations)[0]

    if len(registrations) > 1:
        logger.warning(
            f"XNAT has {len(registrations)} PACS registrations; expected one. "
            f"Using '{chosen.get('aeTitle')}'"
            f"{' (the default query/retrieve PACS)' if default_qr else ' (first listed)'}."
        )

    try:
        _resolved_pacs_id = int(chosen["id"])
    except (KeyError, TypeError, ValueError) as e:
        logger.error(f"PACS registration from XNAT has no usable id ({e}); falling back to id {PACS_ID}.")
        return PACS_ID

    logger.info(f"Resolved PACS '{chosen.get('aeTitle')}' to id {_resolved_pacs_id}")
    return _resolved_pacs_id


def forget_resolved_pacs_id() -> None:
    """
    Clears the cached PACS id so the next call re-reads it from XNAT.

    Called when XNAT reports the resolved PACS missing. ``configure-xnat.sh`` deletes and recreates
    the registration when its AE title changes, which gives it a new id, and nothing restarts
    imaging-api when XNAT is reconfigured — so without this the cache would point at a deleted
    registration until the container was restarted (FLIP#993).
    """
    global _resolved_pacs_id
    _resolved_pacs_id = None


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
        f"{XNAT_URL}/xapi/pacs/{pacs_id}/status",
        headers=headers,
    )
    if response.status_code == 200:
        return PacsStatus(**response.json())
    elif response.status_code == 404:
        raise NotFoundError(f"PACS with ID '{pacs_id}' not found.")
    else:
        raise Exception(f"Failed to ping PACS: {response.text}")


def check_pacs(headers: dict[str, str], pacs_id: int = PACS_ID) -> None:
    """
    Checks if the PACS system is reachable by pinging it.

    Args:
        headers (dict[str, str]): XNAT authentication headers.
        pacs_id (int): PACS ID to check. Default is the PACS_ID from settings.

    Returns:
        None

    Raises:
        imaging_api.utils.exceptions.NotFoundError: If the PACS with the given ID is not found.
        Exception: If there is an error during the ping request or if the PACS is not reachable or is disabled.
    """
    try:
        pacs_status = ping_pacs(pacs_id, headers)
    except NotFoundError:
        # The id we hold no longer exists in XNAT. If it came from the cache it is stale — the PACS
        # was re-registered under a new id while this process stayed up — so drop it and let the
        # next call re-read, rather than failing every import until the container restarts.
        forget_resolved_pacs_id()
        raise NotFoundError(f"PACS with ID '{pacs_id}' not found.")
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

    Args:
        accession_number (str): The accession number to query.
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        list[Study]: A list of Study objects that match the accession number.

    Raises:
        Exception: If there is an error during the query request.
    """
    # Construct DQR Query
    study_query = StudyQuery(accessionNumber=accession_number, pacsId=resolve_pacs_id(headers))

    response = requests.post(
        f"{XNAT_URL}/xapi/dqr/query/studies",
        headers=headers,
        json=study_query.model_dump(by_alias=True),
    )
    logger.debug(f"Query response: {response.text} - {response.status_code} - {response.reason}")

    if response.status_code == 200:
        logger.info("Successfully queried PACS via DQR")
    elif response.status_code == 204:
        logger.warning("No studies found via DQR")
        return []
    elif response.status_code == 401:
        raise Exception("Unauthorized to query PACS via DQR")
    else:
        raise Exception("Failed to query PACS via DQR")

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
        imaging_api.utils.exceptions.NotFoundError: If the project with the given ID is not found or if no studies are
        found on PACS.
        Exception: If there is an error during the import request.
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

    # Retrieve against the same PACS the C-FIND queried. The model default is the static fallback,
    # so resolve here rather than leaving the import pointing at a different registration than the
    # query used (FLIP#993).
    import_request.pacs_id = resolve_pacs_id(headers)

    # Check PACS
    check_pacs(headers=headers, pacs_id=import_request.pacs_id)

    # Send import request to DQR
    response = requests.post(
        f"{XNAT_URL}/xapi/dqr/import",
        headers=headers,
        json=import_request.model_dump(by_alias=True),
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
