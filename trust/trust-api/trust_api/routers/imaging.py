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

from fastapi import APIRouter

from trust_api.config import get_settings
from trust_api.routers.schemas import CentralHubProject, UpdateProfileRequest
from trust_api.utils.http import make_request
from trust_api.utils.logger import logger

IMAGING_API_URL = get_settings().IMAGING_API_URL

router = APIRouter(prefix="/imaging", tags=["Imaging"])


@router.post("")
async def create_imaging_project(central_hub_project: CentralHubProject):
    """
    Create an imaging project from a central hub project.

    Args:
        central_hub_project (trust_api.routers.schemas.CentralHubProject): The central hub project data.
    """
    logger.info(f"Creating imaging project with input: {central_hub_project}")
    response = await make_request(
        "POST",
        f"{IMAGING_API_URL}/projects/create-project-from-central-hub-project",
        json_body=central_hub_project.model_dump(mode="json"),
    )
    logger.info(f"Response from trust-api create imaging project: {response}")
    return response


@router.delete("/{imaging_project_id}")
async def delete_imaging_project(imaging_project_id: str):
    """
    Delete an imaging project by its ID.

    Args:
        imaging_project_id (str): The ID of the imaging project to delete.
    """
    logger.info(f"Deleting imaging project with ID: {imaging_project_id}")
    return await make_request(
        "DELETE",
        f"{IMAGING_API_URL}/projects/",
        params={"project_id": imaging_project_id},
    )


@router.get("/{imaging_project_id}")
async def get_imaging_project_status(imaging_project_id: str, encoded_query: str):
    """
    Get the status of an imaging project by its ID.

    Args:
        imaging_project_id (str): The ID of the imaging project.
        encoded_query (str): Project cohort query base64 url encoded.
    """
    logger.info(f"Retrieving imaging project with ID: {imaging_project_id}")
    response = await make_request(
        "GET",
        f"{IMAGING_API_URL}/retrieval/import_status_count/{imaging_project_id}",
        params={"encoded_query": encoded_query},
    )
    logger.info(f"Response from trust-api imaging project status: {response}")
    return response


@router.put("/reimport/{imaging_project_id}")
async def reimport_studies(imaging_project_id: str, encoded_query: str):
    """
    Reimport studies for a given imaging project.

    Args:
        imaging_project_id (str): The ID of the imaging project.
        encoded_query (str): Project cohort query base64 url encoded.
    """
    logger.info(f"Reimporting studies for imaging project with ID: {imaging_project_id}")
    return await make_request(
        "PUT",
        f"{IMAGING_API_URL}/retrieval/reimport_imaging_project_studies/{imaging_project_id}",
        params={"encoded_query": encoded_query},
    )


@router.put("/users")
async def update_profile(update_profile_request: UpdateProfileRequest):
    """
    Update the profile of a user.

    Args:
        update_profile_request (UpdateProfileRequest): The request data for updating the profile.
    """
    logger.info(f"Updating profile with input: {update_profile_request}")
    return await make_request(
        "PUT",
        f"{IMAGING_API_URL}/users",
        json_body=update_profile_request.model_dump(mode="json"),
    )
