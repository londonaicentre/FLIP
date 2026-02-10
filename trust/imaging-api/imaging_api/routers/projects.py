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
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from imaging_api.routers.schemas import (
    CentralHubProject,
    CreatedProject,
    Experiment,
    Project,
    Subject,
)
from imaging_api.services.projects import (
    add_central_hub_users_to_project,
    create_project,
    delete_project,
    enable_project_command,
    get_all_projects,
    get_experiment,
    get_experiments,
    get_project,
    get_subjects,
    set_project_prearchive_settings,
    to_create_project,
)
from imaging_api.services.retrieval import retrieve_images_for_project
from imaging_api.utils.auth import get_xnat_auth_headers
from imaging_api.utils.exceptions import AlreadyExistsError, NotFoundError
from imaging_api.utils.logger import logger

router = APIRouter(prefix="/projects", tags=["Projects"])

XNATAuthHeaders = Annotated[dict[str, str], Depends(get_xnat_auth_headers)]


@router.get("/", summary="Get XNAT Projects")
def get_projects(headers: XNATAuthHeaders) -> list[Project]:
    """
    Retrieves a list of all projects in XNAT.

    Args:
        headers (XNATAuthHeaders): XNAT authentication headers.

    Returns:
        list[imaging_api.routers.schemas.Project]: List of projects in XNAT.

    Raises:
        HTTPException: If there is an error during the retrieval of projects.
    """
    try:
        return get_all_projects(headers)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/", summary="Create XNAT Project")
def create_project_endpoint(
    project_id: str,
    project_secondary_id: str,
    project_name: str,
    *,
    project_description: str = "",
    headers: XNATAuthHeaders,
) -> Project:
    """
    Creates a new project in XNAT.

    Args:
        project_id (str): Unique identifier for the project.
        project_secondary_id (str): Secondary identifier for the project.
        project_name (str): Name of the project.
        project_description (str, optional): Description of the project.
        headers (XNATAuthHeaders): XNAT authentication headers.

    Returns:
        imaging_api.routers.schemas.Project: The created project object.

    Raises:
        HTTPException: If there is an error during the creation of the project.
    """
    try:
        return create_project(project_id, project_secondary_id, project_name, project_description, headers)
    except AlreadyExistsError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/create-project-from-central-hub-project",
    response_model=CreatedProject,
    summary="Create XNAT Project from Central Hub Project information",
)
async def create_project_from_central_hub_project(
    central_hub_project: CentralHubProject, headers: XNATAuthHeaders, background_tasks: BackgroundTasks
) -> CreatedProject:
    """
    Creates a new project in XNAT based on Central Hub project information.
    Central Hub project information includes the project ID, project name, and users.

    A dedicated XNAT project ID will be created, as well as corresponding users.
    If users already exist on XNAT with those emails, they will be added to the project.
    If users do not exist on XNAT, they will be created and added to the project.

    Args:
        central_hub_project (imaging_api.routers.schemas.CentralHubProject): Central Hub project object including its
        ID, name, and users.
        headers (XNATAuthHeaders): XNAT authentication headers.
        background_tasks (BackgroundTasks): Background task manager for retrieval scheduling.

    Returns:
        CreatedProject (imaging_api.routers.schemas.CreatedProject): The created project object.

    Raises:
        HTTPException: If there is an error during the creation of the project.
    """
    # Map central hub project to XNAT project 'create' request object
    project_data = to_create_project(central_hub_project)

    try:
        project = create_project(
            project_data.id,
            project_data.secondary_id,
            project_data.name,
            project_data.description,
            headers,
        )
    except AlreadyExistsError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to create project from central hub project: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

    # Set the project pre-archive settings
    set_project_prearchive_settings(project.ID, headers)

    # Enable dcm2niix command at the project level
    # TODO We may want to add more here in the future, e.g. QC
    enable_project_command(project.ID, "xnat/dcm2niix:latest", headers)

    # Add central hub users to imaging project
    # Will create XNAT users if they do not exist, and add them to the XNAT project
    created_users, added_users = add_central_hub_users_to_project(central_hub_project, project.ID, headers)

    # Retrieve images from PACS using the retrieval service
    # Schedule retrieval in background (non-blocking)
    background_tasks.add_task(
        retrieve_images_for_project,
        project_id=project.ID,
        query=central_hub_project.query,
        headers=headers,
    )

    return CreatedProject(
        ID=UUID(project.ID),
        name=project.name,
        created_users=created_users,
        added_users=added_users,
    )


@router.delete("/{project_id}", summary="Delete XNAT Project")
async def delete_project_endpoint(project_id: str, headers: XNATAuthHeaders) -> Project:
    """
    Deletes an existing project in XNAT.

    Args:
        project_id (str): Unique identifier for the project.
        headers (XNATAuthHeaders): XNAT authentication headers.

    Returns:
        Project: The deleted project object.

    Raises:
        HTTPException: If there is an error during the deletion of the project.
    """
    try:
        return await delete_project(project_id, headers)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to delete project {project_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# get project details endpoint
@router.get(
    "/{project_id}",
    summary="Get XNAT Project Details",
    description="Get details of a specific project on XNAT.",
)
def get_project_endpoint(project_id: str, headers: XNATAuthHeaders) -> Project:
    """
    Retrieves details of a specific project in XNAT.

    The reason we are not using the specific XNAT endpoint ``GET /data/projects/{id}`` is because it returns something
    like the below, which I am not sure is very useful, is missing fields like ``pi_firstname``, ``pi_lastname``,
    ``URI`` which we may want to retrieve.

    .. code-block:: json

        {
            "project": {
                "items": [
                {
                    "children": [],
                    "meta": {
                    "create_event_id": 270,
                    "xsi:type": "xnat:projectData",
                    "isHistory": false,
                    "start_date": "Mon Mar 24 18:21:46 UTC 2025"
                    },
                    "data_fields": {
                    "secondary_ID": "dc5c1758-1a4d-4fca-80ce-fa208d11874d",
                    "name": "string:3fa85f64-5717-4562-b3fc-2c963f66afa6-FL-Project",
                    "description": "Project corresponding to central hub project 3fa85f64-5717-4562-b3fc-2c963f66afa6",
                    "active": "true",
                    "ID": "dc5c1758-1a4d-4fca-80ce-fa208d11874d"
                    }
                }
                ]
            }
        }

    Args:
        project_id (str): Unique identifier for the project.
        headers (XNATAuthHeaders): XNAT authentication headers.

    Returns:
        Project: The project object.

    Raises:
        HTTPException: If there is an error during the retrieval of the project.
    """
    try:
        return get_project(project_id, headers)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{project_id}/subjects", summary="Get XNAT Project Subjects")
def get_project_subjects_endpoint(project_id: str, headers: XNATAuthHeaders) -> List[Subject]:
    """
    Retrieves a list of subjects in a specific project in XNAT.

    Args:
        project_id (str): Unique identifier for the project.
        headers (XNATAuthHeaders): XNAT authentication headers.

    Returns:
        List[Subject]: List of subjects in the project.

    Raises:
        HTTPException: If there is an error during the retrieval of subjects for the project.
    """
    try:
        return get_subjects(project_id, headers)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to retrieve subjects for project {project_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{project_id}/experiments", summary="Get XNAT Project Experiments")
def get_project_experiments_endpoint(project_id: str, headers: XNATAuthHeaders) -> List[Experiment]:
    """
    Retrieves a list of experiments in a specific project in XNAT.

    Args:
        project_id (str): Unique identifier for the project.
        headers (XNATAuthHeaders): XNAT authentication headers.

    Returns:
        List[Experiment]: List of experiments in the project.

    Raises:
        HTTPException: If there is an error during the retrieval of experiments for the project.
    """
    try:
        return get_experiments(project_id, headers)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to retrieve experiments for project {project_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# get project experiment (Get experiment data for a given experiment id and project id)
@router.get(
    "/{project_id}/experiment/{experiment_id_or_label}",
    summary="Get XNAT Project Experiment",
    description="Get details of a specific experiment in a project in XNAT from the experiment ID or label.",
    response_model=dict,
)
def get_project_experiment_endpoint(project_id: str, experiment_id_or_label: str, headers: XNATAuthHeaders) -> dict:
    """
    Retrieves details of a specific experiment in a project in XNAT.

    Args:
        project_id (str): Unique identifier for the project.
        experiment_id_or_label (str): Unique identifier for the experiment.
        headers (XNATAuthHeaders): XNAT authentication headers.

    Returns:
        dict: The experiment object.

    Raises:
        HTTPException: If there is an error during the retrieval of the experiment.
    """
    try:
        return get_experiment(project_id, experiment_id_or_label, headers)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to retrieve experiment {experiment_id_or_label} for project {project_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
