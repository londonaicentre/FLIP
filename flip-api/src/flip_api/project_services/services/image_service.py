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

import base64
import json  # For serializing request data if needed by http client
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from uuid import UUID

import httpx
from sqlalchemy.exc import SQLAlchemyError  # For more specific DB error handling
from sqlmodel import Session, col, select, update

from flip_api.db.models.main_models import Trust as DBTrust
from flip_api.db.models.main_models import XNATProjectStatus  # Assuming these exist
from flip_api.domain.interfaces.project import (
    IImagingStatus,
    IImagingStatusResponse,
    IReimportQuery,
    IUpdateXnatProfile,
)
from flip_api.domain.schemas.projects import ImagingProject, XnatProjectStatusInfo
from flip_api.domain.schemas.status import XNATImageStatus
from flip_api.trusts_services.services.trust import get_trusts
from flip_api.utils.http import trust_ssl_context
from flip_api.utils.logger import logger


def to_utc_aware(dt: datetime | None) -> datetime:
    """Convert a datetime to a timezone-aware UTC datetime. If the input is None, returns the minimum datetime."""
    if dt is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


# Helper for Base64 URL encoding
def base64_url_encode(data: str) -> str:
    """Encode a string using Base64 URL encoding without padding."""
    return base64.urlsafe_b64encode(data.encode("utf-8")).decode("utf-8").rstrip("=")


def get_imaging_projects(project_id: UUID, db: Session) -> List[ImagingProject]:
    """
    Retrieve imaging projects associated with a given project ID.

    Args:
        project_id (UUID): The ID of the project to retrieve imaging projects for.
        db (Session): The database session for executing queries.

    Returns:
        List[ImagingProject]: A list of ImagingProject objects associated with the given project ID.

    Raises:
        SQLAlchemyError: If there is an error executing the database query.
        Exception: If there is an unexpected error during the retrieval process.
    """
    try:
        # Columns must match the order expected by ImagingProject constructor or mapping logic
        statement = (
            select(  # type: ignore[call-overload]
                XNATProjectStatus.id,
                XNATProjectStatus.xnat_project_id,
                XNATProjectStatus.trust_id,
                XNATProjectStatus.retrieve_image_status,
                DBTrust.name,
                DBTrust.endpoint,
                XNATProjectStatus.reimport_count,
            )
            .join(DBTrust, col(XNATProjectStatus.trust_id) == DBTrust.id)
            .where(col(XNATProjectStatus.project_id) == project_id)
        )
        results = db.exec(statement).all()
        logger.debug(f"Imaging projects fetched for project_id {project_id}: {results}")

        imaging_projects = [
            ImagingProject(
                id=row[0],
                xnat_project_id=row[1],
                trust_id=row[2],
                retrieve_image_status=XNATImageStatus(row[3]),
                name=row[4],
                endpoint=row[5],
                reimport_count=row[6],
            )
            for row in results
        ]
        return imaging_projects
    except SQLAlchemyError as e:
        logger.error(f"Database error fetching imaging projects for project_id {project_id}: {e}", exc_info=True)
        raise
    except Exception as e:  # Catch other unexpected errors
        logger.error(f"Unexpected error fetching imaging projects for project_id {project_id}: {e}", exc_info=True)
        raise


def delete_imaging_project(imaging_project: ImagingProject, db: Session) -> bool:
    """
    Delete an imaging project via its API endpoint and update its status in the database.

    Args:
        imaging_project (ImagingProject): The imaging project to delete.
        db (Session): The database session for executing queries.

    Returns:
        bool: True if the deletion and status update were successful, False otherwise.
    """
    try:
        trust_endpoint = f"{imaging_project.endpoint}/imaging/{imaging_project.xnat_project_id}"

        with httpx.Client(timeout=10.0, verify=trust_ssl_context()) as client:
            response = client.delete(trust_endpoint)
            logger.debug(f"Delete request to {trust_endpoint} returned status {response.status_code}")

        statement = (
            update(XNATProjectStatus)
            .where(col(XNATProjectStatus.id) == imaging_project.id)
            .values(retrieve_image_status=XNATImageStatus.DELETED.value)
        )
        db.execute(statement)
        db.commit()
        logger.info(
            f"Successfully marked imaging project {imaging_project.xnat_project_id} (ID: {imaging_project.id}) as "
            "DELETED."
        )
        return True
    except Exception as e:
        logger.error(f"Error deleting imaging project via API or updating DB: {e}", exc_info=True)
        db.rollback()
        return False


def get_xnat_project_status_info(xnat_project_id: UUID, db: Session) -> Optional[XnatProjectStatusInfo]:
    """
    Retrieve the XNAT project status information for a given XNAT project ID.

    Args:
        xnat_project_id (UUID): The ID of the XNAT project to retrieve status information for.
        db (Session): The database session for executing queries.

    Returns:
        Optional[XnatProjectStatusInfo]: An object containing the XNAT project status information, or None if the
                                         project status could not be found.

    Raises:
        SQLAlchemyError: If there is an error executing the database query.
        Exception: If there is an unexpected error during the retrieval process.
    """
    try:
        statement = select(XNATProjectStatus.retrieve_image_status, XNATProjectStatus.reimport_count).where(
            XNATProjectStatus.xnat_project_id == xnat_project_id
        )

        result_row = db.exec(statement).one_or_none()

        if not result_row:
            logger.error(f"Could not get XNAT status for project id: {xnat_project_id}")
            return None

        return XnatProjectStatusInfo(retrieve_image_status=XNATImageStatus(result_row[0]), reimport_count=result_row[1])
    except SQLAlchemyError as e:
        logger.error(f"Database error fetching XNAT project status for {xnat_project_id}: {e}", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"Unexpected error fetching XNAT project status for {xnat_project_id}: {e}", exc_info=True)
        raise


def get_imaging_project_statuses(
    imaging_projects: List[ImagingProject], encoded_query: str, db: Session
) -> List[IImagingStatus]:
    """
    Retrieve the imaging project statuses for a list of imaging projects.

    Args:
        imaging_projects (List[ImagingProject]): The list of imaging projects to retrieve statuses for.
        encoded_query (str): The Base64 URL encoded query to send to the imaging project endpoints.
        db (Session): The database session for executing queries.

    Returns:
        List[IImagingStatus]: A list of IImagingStatus containing the status information for each imaging project.
    """
    logger.debug(
        f"Attempting to retrieve the imaging project status. Trusts requested: {[ip.name for ip in imaging_projects]}"
    )
    response_statuses: List[IImagingStatus] = []

    for row_project in imaging_projects:
        xnat_status_info = get_xnat_project_status_info(row_project.xnat_project_id, db)
        logger.debug(f"Retrieved XNAT status info for project {row_project.xnat_project_id}: {xnat_status_info}")

        project_creation_completed = False
        reimport_count_val = 0
        if xnat_status_info:
            project_creation_completed = xnat_status_info.retrieve_image_status == XNATImageStatus.CREATED
            reimport_count_val = xnat_status_info.reimport_count

        current_project_status = IImagingStatus(
            trust_id=row_project.trust_id,
            trust_name=row_project.name,
            project_creation_completed=project_creation_completed,
            reimport_count=reimport_count_val,
            import_status=None,
        )  # type: ignore[call-arg]

        try:
            logger.debug(f"Encoded query: {encoded_query}")
            api_url = f"{row_project.endpoint}/imaging/{row_project.xnat_project_id}"
            with httpx.Client(timeout=10.0, verify=trust_ssl_context()) as client:
                response = client.get(api_url, params={"encoded_query": encoded_query})
            logger.debug(f"API response for {row_project.name}: {response.status_code} - {response.text}")

            # Assuming trust_api_response.data is a dict that can be parsed by IImagingStatusResponse
            if response.status_code == 200 and response.json():
                parsed_import_status = IImagingStatusResponse.model_validate(response.json())
                current_project_status.import_status = parsed_import_status.import_status
            else:
                logger.error(f"Failed API call or no data for {row_project.name}. Status: {response.status_code}")

        except httpx.RequestError as e:
            logger.error(f"Request to {api_url} failed: {str(e)}")
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error for {api_url}: {e.response.status_code}")

        response_statuses.append(current_project_status)

    if response_statuses:
        logger.info(f"Imaging statuses retrieved: {len(response_statuses)} trusts reported.")
        logger.debug(f"Response statuses: {response_statuses}")
    else:
        logger.error("No trusts reported back with the status. An empty list will be returned")

    return response_statuses


def update_xnat_user_profile(
    request_data: IUpdateXnatProfile,
    db: Session,
) -> None:
    """
    Update a user's profile across all trusts' XNAT instances.

    Args:
        request_data (IUpdateXnatProfile): The user profile data to update.
        db (Session): The database session for retrieving trust information.

    Returns:
        None
    """
    logger.debug(f"Attempting to update XNAT user profile: {request_data.email} at all trusts")

    trusts = get_trusts(db)
    trusts_responses: List[dict] = []

    for trust in trusts:
        try:
            with httpx.Client(timeout=10.0, verify=trust_ssl_context()) as client:
                response = client.put(
                    f"{trust.endpoint}/imaging/users",
                    json=request_data.model_dump(mode="json"),
                )
            trusts_responses.append({
                "trust_name": trust.name,
                "status": response.status_code,
                "status_text": response.text,  # Assuming statusText on response
                "data": response.json(),  # Assuming data on response
            })
        except Exception as error:
            logger.error(f"Unable to update XNAT user profile '{request_data.email}' at {trust.name} | Error: {error}")
            trusts_responses.append({"trust_name": trust.name, "status": "ERROR", "error_message": str(error)})

    if trusts_responses:
        logger.info(f"XNAT user profile update responses: {json.dumps(trusts_responses)}")
    else:
        logger.error("No trusts to update or no responses received.")


def reimport_failed_studies(
    reimport_queries: List[IReimportQuery],
    db: Session,
    project_reimport_rate_minutes: int,
) -> bool:
    """
    Reimport failed studies for a list of reimport queries, ensuring that reimports are only attempted if the
    specified time interval has passed since the last reimport.

    Args:
        reimport_queries (List[IReimportQuery]): A list of queries containing information about which projects and
            trusts to reimport studies for, along with the last reimport time.
        db (Session): The database session for updating reimport status.
        project_reimport_rate_minutes (int): The minimum number of minutes that must have passed since the last
            reimport before attempting another reimport for the same project and trust.

    Returns:
        bool: True if all eligible reimport attempts were successful, False if any eligible reimport attempt failed.
    """
    successful_reimports_count = 0
    total_eligible_queries = 0

    # TODO determine if we need to use a private API key for the reimport endpoint
    # private_api_key = get_settings().PRIVATE_API_KEY

    for query in reimport_queries:
        encoded_query = base64_url_encode(query.query)  # query from IReimportQuery
        url = f"{query.trust_endpoint}/imaging/reimport/{query.xnat_project_id}"

        last_reimport_time_utc = to_utc_aware(query.last_reimport)
        reimport_interval = timedelta(minutes=project_reimport_rate_minutes)

        now_utc = datetime.now(timezone.utc)
        next_eligible = last_reimport_time_utc + reimport_interval
        if now_utc <= next_eligible:
            logger.info(
                "Time specified in PROJECT_REIMPORT_RATE has not been exceeded for project "
                f"{query.xnat_project_id} at trust {query.trust_name} "
                f"(Last reimport: {last_reimport_time_utc}, Next eligible: {next_eligible})"
            )
            continue

        # Eligible queries are the ones that pass the time check
        total_eligible_queries += 1

        try:
            with httpx.Client(timeout=10.0, verify=trust_ssl_context()) as client:
                response = client.put(url, params={"encoded_query": encoded_query})
                response.raise_for_status()

            logger.info(f"Successfully initiated reimport for {query.xnat_project_id} at {query.trust_name}")

            # Update the last_reimport and increment reimport_count in the database
            xnat_project_status = db.exec(
                select(XNATProjectStatus)
                .where(XNATProjectStatus.trust_id == query.trust_id)
                .where(XNATProjectStatus.xnat_project_id == query.xnat_project_id)
            ).one_or_none()

            if xnat_project_status:
                xnat_project_status.last_reimport = datetime.utcnow()
                xnat_project_status.reimport_count += 1
                db.commit()
                db.refresh(xnat_project_status)
                successful_reimports_count += 1
            else:
                logger.error(
                    f"Could not find XNATProjectStatus record to update for project {query.xnat_project_id} "
                    f"at trust {query.trust_name}"
                )
                continue  # Move to next query

        except httpx.HTTPStatusError as e:
            logger.error(
                "Failed to initiate reimport for project %s at %s. Status: %s, Response: %s",
                query.xnat_project_id,
                query.trust_name,
                e.response.status_code,
                e.response.text,
            )

        except Exception as error:
            logger.error(
                "There was an unexpected error when reimporting studies for project %s at trust %s: %s",
                query.xnat_project_id,
                query.trust_name,
                error,
                exc_info=True,
            )
            # db.rollback() # Rollback if an error occurs before commit for this item
            # No, commit is per item, so only previous successful items are committed.
            continue  # Move to next query

    if total_eligible_queries > 0 and successful_reimports_count < total_eligible_queries:
        logger.error("Not all eligible reimport requests resulted in a successful response and DB update.")
        return False

    if total_eligible_queries == 0:
        logger.info("No queries were eligible for reimport at this time.")
        return True  # No failures if no queries were eligible

    logger.info(
        f"Reimport process completed. Successful reimports: {successful_reimports_count}/{total_eligible_queries}"
    )
    return True
