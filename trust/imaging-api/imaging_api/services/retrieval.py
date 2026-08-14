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

from typing import Annotated

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
from imaging_api.services_external.data_access import get_accession_ids
from imaging_api.utils.auth import get_xnat_auth_headers
from imaging_api.utils.encryption import encrypt
from imaging_api.utils.exceptions import CohortBelowThresholdError, NotFoundError
from imaging_api.utils.logger import logger

XNATAuthHeaders = Annotated[dict[str, str], Depends(get_xnat_auth_headers)]

# Terminal-failure status values. Re-check on any XNAT upgrade, since a renamed value would silently
# misclassify a failed import as in-flight `processing`:
#   - queued/executed PACS requests (DQR plugin):  PacsRequest.FAILED_STATUS_TEXT == "FAILED"
#   - directArchive sessions (XNAT prearchive):    PrearcUtils.PrearcStatus.ERROR
# `PrearcStatus.ERROR` re-verified present in the XNAT 1.10.0 source. The DQR value IS verified for
# DQR 3.0.0 (the 1.10-targeting major bump that also swaps dcm4che2 for dcm4che5): the released JAR
# (public download on bitbucket.org/xnatdev/dicom-query-retrieve) keeps
# `PacsRequest.FAILED_STATUS_TEXT == "FAILED"` (full vocabulary: QUEUED/PROCESSING/ISSUED/RECEIVED/FAILED),
# and the `xhbm_*_pacs_request` columns read by imaging_api/db/get_*_pacs_request_by_project.py were
# confirmed unchanged against a live 3.0.0 instance during the 1.10.0 upgrade smoke test.
# Any other status on a row that exists means the import is still in flight. A successful directArchive
# deletes its row, so "successful" is never read from these tables — only from the experiment listing.
_PACS_REQUEST_FAILED_STATUS = "FAILED"
_DIRECT_ARCHIVE_FAILED_STATUS = "ERROR"


async def retrieve_images_for_project(project_id: str, query: str, headers: XNATAuthHeaders) -> bool:
    """
    Queues an import request for the images associated with the given project.

    Steps:

    1. Encrypt project ID to send to the data access API
    2. Fetch the cohort's accession IDs from the data access API
    3. Query PACS to find study for each accession ID -> put into a list
    4. Make a ImportStudyRequest object with the list of studies
    5. Check response gives all to QUEUED

    Args:
        project_id (str): The imaging project ID to retrieve the data about.
        query (str): The cohort query (raw SQL).
        headers (XNATAuthHeaders): The headers containing XNAT authentication details.

    Returns:
        bool: True if all studies were successfully queued, False otherwise — including when
        the cohort is below the trust's ``COHORT_QUERY_THRESHOLD``, in which case the Data
        Access API withholds the accession IDs and nothing is imported.

    Raises:
        HTTPException: If the request cannot be processed.
    """
    # Check if project exists
    try:
        get_project(project_id, headers)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Get accession IDs from data access API. The endpoint projects the cohort
    # query to the accession_id column server-side, so no other columns are
    # transmitted across the trust boundary.
    encrypted_project_id = encrypt(project_id)
    try:
        accession_ids: list[str] = await get_accession_ids(encrypted_project_id, query)
    except CohortBelowThresholdError:
        # A settled outcome, not a failure to retry: the trust will not release identifiers
        # for a cohort this small, so there is nothing to import. Logged rather than raised
        # because this runs as a background task — the create-project response has already
        # been sent, so an exception here would only reach the server log as a traceback.
        logger.warning(
            f"Not importing images for project {project_id}: the cohort is below the trust's "
            "minimum size, so the Data Access API withheld its accession IDs."
        )
        return False

    studies_list: list[ImportStudy] = []

    total_accessions = len(accession_ids)
    for idx, accession_number in enumerate(accession_ids, 1):
        # TODO Unlike in the old repo, accession numbers are now not encrypted in OMOP database.
        # accession_number = decrypt(encrypted_accession_number)

        logger.info(f"Querying PACS for accession number {idx}/{total_accessions}")

        try:
            studies_found = query_by_accession_number(accession_number, headers)
        except Exception as e:
            # Class-only: exception text here can embed the accession number or
            # study metadata (e.g. a pydantic ValidationError renders its input,
            # a requests error renders the full URL) — logging policy.
            logger.error(
                f"Unexpected error querying PACS for accession number {idx}/{total_accessions}: {type(e).__name__}"
            )
            continue

        # TODO What if multiple studies are found here for a given accession number?
        # We should probably introduce a way to handle this by e.g. filtering data
        # For now, we will just take the first one
        if not studies_found:
            logger.warning(f"No study found for accession number {idx}/{total_accessions}")
            continue
        else:
            study = studies_found[0]
            # If multiple studies are found, log a warning and use the first one, for now.
            if len(studies_found) > 1:
                logger.warning(
                    f"Multiple studies found for accession number {idx}/{total_accessions}. Using the first one."
                )

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

    Known limitation: only *terminal* failures (executed `FAILED`, directArchive `ERROR`) are
    reported as `Failed`. A study wedged in a non-terminal state with no further transition — e.g.
    executed `ISSUED` (the C-MOVE was issued but objects never arrive) or a directArchive stuck at
    `RECEIVING`/`BUILDING` (a hung build that neither errors nor deletes its row) — stays `Processing`
    indefinitely and is therefore never retried. Detecting "stuck" would need a staleness window
    (the rows carry `timestamp`); not implemented here.

    1. Fetch the cohort's accession IDs from the data access API
    2. Get project experiments
    3. Get project import status

    Args:
        project_id (str): The imaging project ID to retrieve the data about.
        query (str): The cohort query.
        headers (XNATAuthHeaders): The headers containing XNAT authentication details.

    Returns:
        ImportStatus: An object containing the status of study imports.

    Raises:
        HTTPException: 403 if the cohort has fallen below the trust's ``COHORT_QUERY_THRESHOLD``
            so its accession IDs cannot be released — note the cohort query is re-run against
            live OMOP on every status check, so a project that imported cleanly can start
            refusing later if its cohort shrinks (see FLIP#857). Otherwise, if the request
            cannot be processed.
    """
    # Encrypt project ID to send to the data access API
    encrypted_project_id = encrypt(project_id)

    # Get accession IDs from data access API (server-side projection — no other
    # cohort columns leave the trust).
    try:
        accession_ids: list[str] = await get_accession_ids(encrypted_project_id, query)
    except CohortBelowThresholdError as e:
        # Unlike the import path, this one has a caller waiting on a response, so surface the
        # reason: trust-api relays this detail to the hub, and without it the per-trust status
        # shows only a raw transport error for what is actually a policy decision.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Cohort is below the trust's minimum size, so its accession IDs cannot be "
                "released and import status cannot be reported."
            ),
        ) from e

    # Fetches a list of XNAT experiments associated with a given XNAT project.
    experiments = get_experiments(project_id, headers)
    experiments_df = pd.DataFrame([exp.model_dump(by_alias=True) for exp in experiments])

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

    # Partition the DQR records into terminal-failure vs still-in-flight accessions. A terminal failure
    # (executed PACS request FAILED, or directArchive session ERROR) must be reported as `failed` so it
    # is surfaced to the operator and retried — otherwise it lingers indefinitely as `processing`. Any
    # other status on a record that exists means the import is still in flight. Note: an accession can be
    # both received (executed RECEIVED) and failed (directArchive ERROR); `failed` takes precedence below.
    # A directArchive session's accession label is `name` (set for DQR imports); fall back to
    # `folder_name` (which carries the same label) when `name` is NULL, so a NULL-named archive
    # failure is still attributed to its accession rather than silently dropped.
    failed_accessions = {
        (session.name or session.folder_name)
        for session in direct_archive_sessions
        if session.status == _DIRECT_ARCHIVE_FAILED_STATUS
    } | {req.accession_number for req in executed_pacs_requests if req.status == _PACS_REQUEST_FAILED_STATUS}
    in_progress_accessions = {
        (session.name or session.folder_name)
        for session in direct_archive_sessions
        if session.status != _DIRECT_ARCHIVE_FAILED_STATUS
    } | {req.accession_number for req in executed_pacs_requests if req.status != _PACS_REQUEST_FAILED_STATUS}
    queued_accessions = {req.accession_number for req in queued_pacs_requests}

    for accession_number in accession_ids:
        # TODO Unlike in the old repo, accession numbers are now not encrypted in OMOP database, so no need to decrypt
        # here.

        # "successful" means archived as a queryable experiment — a directArchive that has only been
        # RECEIVED is not yet archived, so it stays `processing` until the experiment appears.
        if accession_number in successfully_imported_accession_numbers:
            import_status.successful.append(accession_number)
        elif accession_number in failed_accessions:
            import_status.failed.append(accession_number)
        elif accession_number in in_progress_accessions:
            import_status.processing.append(accession_number)
        elif accession_number in queued_accessions:
            import_status.queued.append(accession_number)
        else:
            # Never entered the DQR queue (e.g. no matching study on PACS).
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

    Raises:
        HTTPException: If the request cannot be processed.
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
    logger.info(f"Retrying import for {len(failed_accession_numbers)} failed accession numbers")

    # For each accession ID, unencrypt it and find the study
    studies_list: list[ImportStudy] = []

    total_retries = len(failed_accession_numbers)
    for idx, accession_number in enumerate(failed_accession_numbers, 1):
        # TODO Unlike in the old repo, accession numbers are now not encrypted in OMOP database.
        # accession_number = decrypt(encrypted_accession_number)

        logger.info(f"Querying PACS for accession number {idx}/{total_retries}")

        try:
            studies_found = query_by_accession_number(accession_number, headers)
        except Exception as e:
            # Class-only: exception text here can embed the accession number or
            # study metadata (logging policy).
            logger.error(
                f"Unexpected error querying PACS for accession number {idx}/{total_retries}: {type(e).__name__}"
            )
            continue

        # TODO What if multiple studies are found here for a given accession number?
        # We should probably introduce a way to handle this by e.g. filtering data
        # For now, we will just take the first one
        if not studies_found:
            logger.warning(f"No study found for accession number {idx}/{total_retries}")
            continue
        else:
            study = studies_found[0]
            # If multiple studies are found, log a warning and use the first one, for now.
            if len(studies_found) > 1:
                logger.warning(
                    f"Multiple studies found for accession number {idx}/{total_retries}. Using the first one."
                )

        logger.info(f"Study found for accession number {idx}/{total_retries}")

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
