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

from typing import Annotated, List

import pandas as pd
from fastapi import Depends, HTTPException, status

from imaging_api.db.get_direct_archive_sessions_by_project import (
    get_direct_archive_sessions_by_project,
)
from imaging_api.db.get_executed_pacs_request_by_project import get_executed_pacs_request_by_project
from imaging_api.db.get_queued_pacs_request_by_project import get_queued_pacs_request_by_project
from imaging_api.db.get_session import get_session
from imaging_api.routers.schemas import (
    ImportStatus,
    ImportStudy,
    ImportStudyRequest,
)
from imaging_api.services.imaging import query_by_accession_number, queue_image_import_request
from imaging_api.services.projects import get_experiments, get_project
from imaging_api.services_external.data_access import get_dataframe
from imaging_api.utils.auth import get_xnat_auth_headers
from imaging_api.utils.encryption import encrypt
from imaging_api.utils.exceptions import NotFoundError
from imaging_api.utils.logger import logger

XNATAuthHeaders = Annotated[dict[str, str], Depends(get_xnat_auth_headers)]


async def retrieve_images_for_project(project_id: str, query: str, headers: XNATAuthHeaders) -> bool:
    """
    Queues an import request for the images associated with the given project.

    Steps:

    1. Encrypt project ID to send to the data access API
    2. Get dataframe from data access API, using the provided query
    3. Get accession IDs, unencrypt them
    4. Query PACS to find study for each accession ID -> put into a list
    5. Make a ImportStudyRequest object with the list of studies
    6. Check response gives all to QUEUED

    Args:
        project_id (str): The imaging project ID to retrieve the data about.
        query (str): The cohort query (raw SQL).
        headers (XNATAuthHeaders): The headers containing XNAT authentication details.

    Returns:
        bool: True if all studies were successfully queued, False otherwise.
    """
    # Check if project exists
    try:
        get_project(project_id, headers)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Get dataframe from data access API
    encrypted_project_id = encrypt(project_id)
    cohort_df = await get_dataframe(encrypted_project_id, query)
    cohort_df.to_csv("dataframe.csv")

    # QC check if cohort dataframe has an accession_id column
    accession_id_column = "accession_id"
    if accession_id_column not in cohort_df.columns:
        raise HTTPException(
            status_code=400,
            detail=f"Dataframe does not contain a column '{accession_id_column}' [{cohort_df.columns.tolist()}]",
        )

    # For each accession ID, unencrypt it and find the study
    accession_ids: list[str] = cohort_df["accession_id"]
    studies_list: List[ImportStudy] = []

    for accession_number in accession_ids:
        # TODO Unlike in the old repo, accession numbers are now not encrypted in OMOP database.
        # accession_number = decrypt(encrypted_accession_number)

        logger.info(f"Querying PACS for accession number: {accession_number}")

        try:
            studies_found = query_by_accession_number(accession_number, headers)
        except Exception as e:
            logger.error(f"Unexpected error querying PACS with accession number: {accession_number}: {e}")
            continue

        # TODO What if multiple studies are found here for a given accession number?
        # We should probably introduce a way to handle this by e.g. filtering data
        # For now, we will just take the first one
        if not studies_found:
            logger.warning(f"No study found for accession number: {accession_number}")
            continue
        else:
            study = studies_found[0]
            # If multiple studies are found, log a warning and use the first one, for now.
            if len(studies_found) > 1:
                logger.warning(f"Multiple studies found for accession number {accession_number}. Using the first one.")

        import_study = ImportStudy(
            studyInstanceUid=study.study_instance_uid,
            accessionNumber=study.accession_number,
        )
        studies_list.append(import_study)

    # Check that we have at least one study to import
    if not studies_list:
        logger.warning(f"No studies found to import for project {project_id}")
        return False

    logger.info(f"Creating ImportStudyRequest with {len(studies_list)} studies to queue.")
    import_request = ImportStudyRequest(projectId=project_id, studies=studies_list)
    import_response = queue_image_import_request(import_request, headers)

    # Check response gives all to QUEUED
    if all(item.status == "QUEUED" for item in import_response):
        logger.info("All studies queued successfully.")
        return True
    else:
        logger.warning("Some studies failed to queue.")
        return False


async def get_import_status(project_id: str, query: str, headers: XNATAuthHeaders) -> ImportStatus:
    """
    Returns information about the status of study imports:

    * `Successful`: Studies (i.e. accession numbers) successfully imported into XNAT
    * `Processing`: Studies currently being processed
    * `Failed`: Studies that failed to import
    * `Queued`: Studies waiting in the queue
    * `QueueFailed`: Studies that couldn't be queued for import

    1. Get dataframe from data access API
    2. Get project experiments
    3. Get project import status

    Args:
        project_id (str): The imaging project ID to retrieve the data about.
        query (str): The cohort query.
        headers (XNATAuthHeaders): The headers containing XNAT authentication details.

    Returns:
        ImportStatus: An object containing the status of study imports.
    """
    # Encrypt project ID to send to the data access API
    encrypted_project_id = encrypt(project_id)

    # Get dataframe from data access API
    cohort_df = await get_dataframe(encrypted_project_id, query)
    cohort_df.to_csv("dataframe.csv")

    # QC check if cohort dataframe has an accession_id column
    accession_id_column = "accession_id"
    if accession_id_column not in cohort_df.columns:
        raise HTTPException(
            status_code=400,
            detail=f"Dataframe does not contain a column '{accession_id_column}' [{cohort_df.columns.tolist()}]",
        )

    # Fetches a list of XNAT experiments associated with a given XNAT project.
    experiments = get_experiments(project_id, headers)
    experiments_df = pd.DataFrame([exp.model_dump(by_alias=True) for exp in experiments])
    # experiments_df.to_csv("experiments.csv")

    # We need to check if there are any experiments at all in the dataframe
    successfully_imported_accession_numbers: list[str] = (
        experiments_df["label"].to_list() if "label" in experiments_df.columns else []
    )
    logger.debug("Successfully imported accession numbers")

    # TODO What if the XNAT project contains experiments that aren't actually in the cohort dataframe?
    # Could this even happen?

    # Go through the accession numbers and find its import status
    async for session in get_session():
        direct_archive_sessions = await get_direct_archive_sessions_by_project(project_id, session)

    async for session in get_session():
        executed_pacs_requests = await get_executed_pacs_request_by_project(project_id, session)

    async for session in get_session():
        queued_pacs_requests = await get_queued_pacs_request_by_project(project_id, session)

    logger.debug(f"Direct Archive Sessions: {len(direct_archive_sessions)}")
    logger.debug(f"Executed PACS requests: {len(executed_pacs_requests)}")
    logger.debug(f"Queued PACS requests: {len(queued_pacs_requests)}")

    import_status = ImportStatus()

    accession_ids: list[str] = cohort_df["accession_id"]

    for accession_number in accession_ids:
        # TODO Unlike in the old repo, accession numbers are now not encrypted in OMOP database, so no need to decrypt
        # here.

        # Check if the accession number is in the successfully imported list
        if accession_number in successfully_imported_accession_numbers:
            import_status.successful.append(accession_number)
            continue

        # Check if the accession number is in the executed PACS requests
        if accession_number in [req.accession_number for req in executed_pacs_requests]:
            import_status.processing.append(accession_number)
            continue

        # Check if the accession number is in the queued PACS requests
        if accession_number in [req.accession_number for req in queued_pacs_requests]:
            import_status.queued.append(accession_number)
            continue

        # Default if none have been reached
        import_status.queue_failed.append(accession_number)

    # Log the import status
    logger.info(
        "Import status for project %s: QueueFailed=%d, Queued=%d, Processing=%d, Failed=%d Successful=%d",
        project_id,
        len(import_status.queue_failed),
        len(import_status.queued),
        len(import_status.processing),
        len(import_status.failed),
        len(import_status.successful),
    )
    return import_status


async def retry_retrieve_images_for_project(project_id: str, query: str, headers: XNATAuthHeaders) -> bool:
    """
    Queues an import request for the images which have failed to import.

    Steps:

    1. Get import status
    2. If none have status failed (Failed or QueueFailed), return
    3. Create list of accession numbers that have failed.
    4. Unencrypt the accession numbers
    5. Use Pacs Query request to find study for each accession ID -> put into a list
    6. Make a ImportStudyRequest object with the list of studies
    7. Check response gives all to QUEUED
    8. Return success or failure

    Args:
        project_id (str): The imaging project ID to retrieve the data about.
        query (str): The cohort query (raw SQL).
        headers (XNATAuthHeaders): The headers containing XNAT authentication details.

    Returns:
        bool: True if all studies were successfully queued, False otherwise.
    """

    # Check if project exists
    try:
        get_project(project_id, headers)
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    # Get import status
    import_status = await get_import_status(project_id, query, headers)

    # If none have status failed (Failed or QueueFailed), return
    if not import_status.failed and not import_status.queue_failed:
        logger.info(f"No studies to retry import for project {project_id}")
        return True

    # Create list of accession numbers that have failed
    failed_accession_numbers = import_status.failed + import_status.queue_failed
    logger.info(f"Retrying import for accession numbers: {failed_accession_numbers}")

    # For each accession ID, unencrypt it and find the study
    studies_list: List[ImportStudy] = []

    for accession_number in failed_accession_numbers:
        # TODO Unlike in the old repo, accession numbers are now not encrypted in OMOP database.
        # accession_number = decrypt(encrypted_accession_number)

        logger.info(f"Querying PACS for accession number: {accession_number}")

        try:
            studies_found = query_by_accession_number(accession_number, headers)
        except Exception as e:
            logger.error(f"Unexpected error querying PACS with accession number: {accession_number}: {e}")
            continue

        # TODO What if multiple studies are found here for a given accession number?
        # We should probably introduce a way to handle this by e.g. filtering data
        # For now, we will just take the first one
        if not studies_found:
            logger.warning(f"No study found for accession number: {accession_number}")
            continue
        else:
            study = studies_found[0]
            # If multiple studies are found, log a warning and use the first one, for now.
            if len(studies_found) > 1:
                logger.warning(f"Multiple studies found for accession number {accession_number}. Using the first one.")

        logger.info(f"Study found for accession number {accession_number}: {study}")

        import_study = ImportStudy(
            studyInstanceUid=study.study_instance_uid,
            accessionNumber=study.accession_number,
        )
        studies_list.append(import_study)

    # Check that we have at least one study to import
    if not studies_list:
        logger.warning(f"No studies found to import for project {project_id}")
        return False

    logger.info(f"Creating ImportStudyRequest with {len(studies_list)} studies to queue.")
    import_request = ImportStudyRequest(projectId=project_id, studies=studies_list)
    import_response = queue_image_import_request(import_request, headers)

    # Check response gives all to QUEUED
    if all(item.status == "QUEUED" for item in import_response):
        logger.info("All studies queued successfully.")
        return True
    else:
        logger.warning("Some studies failed to queue.")
        return False
