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
        List[Study]: A list of Study objects that match the accession number.

    Raises:
        Exception: If there is an error during the query request.
    """
    # Construct DQR Query
    study_query = StudyQuery(accessionNumber=accession_number, pacsId=PACS_ID)

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
        List[ImportStudyResponse]: A list of ImportStudyResponse objects representing the queued import requests.

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
