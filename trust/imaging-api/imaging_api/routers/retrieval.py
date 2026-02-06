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
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from imaging_api.config import get_settings
from imaging_api.routers.schemas import (
    ImportStatusCount,
    ProjectRetrieval,
)
from imaging_api.services.projects import get_project
from imaging_api.services.retrieval import get_import_status, retry_retrieve_images_for_project
from imaging_api.utils.auth import get_xnat_auth_headers
from imaging_api.utils.exceptions import NotFoundError
from imaging_api.utils.logger import logger

router = APIRouter(prefix="/retrieval", tags=["Retrieval"])

XNATAuthHeaders = Annotated[dict[str, str], Depends(get_xnat_auth_headers)]


def base64_url_decode(data: str) -> str:
    """Decode a Base64 URL-encoded string, adding padding if necessary."""
    # Add padding if necessary
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding).decode("utf-8")


@router.get("/import_status_count/{project_id}")
async def get_import_status_count(project_id: str, encoded_query: str, headers: XNATAuthHeaders) -> ProjectRetrieval:
    """
    Returns a project with details about the status of study imports

    Args:
        project_id (str): The imaging project ID to retrieve the data about.
        encoded_query (str): Project cohort query base64 url encoded.
        headers (XNATAuthHeaders): The headers containing XNAT authentication details.

    Returns:
        ProjectRetrieval: An object containing the status of study imports.

    Raises:
        HTTPException: If the project does not exist or if there was an error while retrieving the project.
    """
    # Check if project exists
    try:
        get_project(project_id, headers)
    except NotFoundError as e:
        logger.error(f"Project not found: {str(e)}")
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error occurred while retrieving project: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

    # Decode the query
    query = base64_url_decode(encoded_query)
    logger.info(f"Decoded query: {query}")

    # Get import status
    import_status = await get_import_status(project_id, query, headers)
    import_status_count = ImportStatusCount(
        successful_count=len(import_status.successful),
        failed_count=len(import_status.failed),
        processing_count=len(import_status.processing),
        queued_count=len(import_status.queued),
        queue_failed_count=len(import_status.queue_failed),
    )
    project_retrieval = ProjectRetrieval(project_creation_completed=True, import_status=import_status_count)
    return project_retrieval


@router.put("/reimport_imaging_project_studies/{project_id}", status_code=status.HTTP_202_ACCEPTED)
async def reimport_imaging_project_studies(
    project_id: str,
    encoded_query: str,
    headers: XNATAuthHeaders,
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    """
    Retries failed study imports for a given imaging project ID and encoded cohort query.

    Args:
        project_id (str): The imaging project ID to retrieve the data about.
        encoded_query (str): Project cohort query base64 url encoded.
        headers (XNATAuthHeaders): The headers containing XNAT authentication details.
        background_tasks (BackgroundTasks): FastAPI BackgroundTasks instance for scheduling the reimport task.

    Returns:
        JSONResponse: A response indicating the result of the reimport operation.

    Raises:
        HTTPException: If the reimport feature is not enabled or if the query is empty
    """
    logger.debug(f"Received request to retry retrieve images for imaging project {project_id}")

    # Check if reimport is enabled
    if not get_settings().REIMPORT_STUDIES_ENABLED:
        msg = "Reimport studies feature is not enabled"
        logger.info(msg)
        raise HTTPException(status_code=status.HTTP_418_IM_A_TEAPOT, detail=msg)

    # Check that query is not empty
    if not encoded_query:
        msg = "Query must not be empty"
        logger.info(msg)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)

    # Decode the query
    query = base64_url_decode(encoded_query)
    logger.info(f"Decoded query: {query}")

    # queue the actual retry work in the background (non-blocking)
    background_tasks.add_task(retry_retrieve_images_for_project, project_id, query, headers)

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED, content={"message": "Reimport queued", "projectId": project_id}
    )
