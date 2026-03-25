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

import urllib.parse
import uuid
from typing import Any

import requests

from imaging_api.config import get_settings
from imaging_api.db.get_queued_pacs_request_by_project import get_queued_pacs_request_by_project
from imaging_api.db.get_session import get_session
from imaging_api.routers.schemas import (
    CentralHubProject,
    CreatedUser,
    CreateProject,
    Experiment,
    Project,
    Subject,
    User,
)
from imaging_api.routers.users import add_user_to_project
from imaging_api.services.users import create_user_from_central_hub_user, get_user_profile_by
from imaging_api.utils.enums import ProjectPreArchiveSettings
from imaging_api.utils.exceptions import AlreadyExistsError, NotFoundError
from imaging_api.utils.logger import logger

XNAT_URL = get_settings().XNAT_URL


def get_project_from_central_hub_project_id(central_hub_project_id: str, headers: dict[str, str]) -> Project:
    """
    Gets the XNAT project from a central hub project ID (corresponds to XNAT project secondary ID)

    Args:
        central_hub_project_id (str): Central hub project ID
        headers (dict[str, str]): XNAT authentication headers

    Returns:
        Project: XNAT project object
    """
    try:
        projects = get_all_projects(headers)
    except Exception as e:
        raise Exception(f"Error: XNAT project fetch failed: {str(e)}")

    for project in projects:
        if project.secondary_ID == central_hub_project_id:
            return project

    raise NotFoundError(f"Project with central hub ID '{central_hub_project_id}' not found among XNAT projects.")


def get_project(project_id: str, headers: dict[str, str]) -> Project:
    """
    Fetches a specific XNAT project by selecting from all projects.

    Args:
        project_id (str): Unique identifier for the project
        headers (dict[str, str]): XNAT authentication headers

    Returns:
        Project: XNAT project object
    """
    try:
        projects = get_all_projects(headers)
    except Exception as e:
        raise Exception(f"Error: XNAT project fetch failed: {str(e)}")

    for project in projects:
        if project.ID == project_id:
            return project

    raise NotFoundError(f"Project with ID '{project_id}' not found.")


def get_all_projects(headers: dict[str, str]) -> list[Project]:
    """
    Fetches all XNAT projects using the correct REST API endpoint.

    Args:
        headers (dict[str, str]): XNAT authentication headers

    Returns:
        List[Project]: List of XNAT project objects
    """
    try:
        response = requests.get(f"{XNAT_URL}/data/projects", headers=headers)
    except Exception as e:
        raise Exception(f"Error: XNAT projects fetch failed: {str(e)}")

    if response.status_code == 200:
        projects = [Project(**project) for project in response.json()["ResultSet"]["Result"]]
        return projects
    else:
        raise Exception(f"Error: XNAT projects fetch failed: {response.status_code} - {response.text}")


def create_payload_for_project_creation(
    xnat_projects_uri: str,
    project_id: str,
    project_secondary_id: str,
    project_name: str,
    project_description: str = "",
) -> str:
    """
    Creates the payload for creating a new project in XNAT.

    Args:
        xnat_projects_uri (str): XNAT projects URI.
        project_id (str): Unique identifier for the project.
        project_secondary_id (str): Secondary ID for the project.
        project_name (str): Name of the project.
        project_description (str, optional): Description of the project.

    Returns:
        str: XML payload for creating the project.
    """
    payload = f"""
    <xnat:projectData xmlns:xnat="{xnat_projects_uri}">
        <ID>{project_id}</ID>
        <secondary_ID>{project_secondary_id}</secondary_ID>
        <name>{project_name}</name>
        <description>{project_description}</description>
    </xnat:projectData>"""
    return payload


def create_project(
    project_id: str,
    project_secondary_id: str,
    project_name: str,
    project_description: str,
    headers: dict[str, str],
) -> Project:
    """
    Core function to create a new project in XNAT.

    Note that ID, secondary_ID and name are required.

    Uses the XNAT REST API endpoint: ``POST - /data/projects``
    Note this endpoint only accepts XML payload, not JSON.
    See also https://wiki.xnat.org/xnat-api/project-api#ProjectAPI-Createoneormoreprojects

    Args:
        project_id (str): Unique identifier for the project.
        project_secondary_id (str): Secondary ID for the project.
        project_name (str): Name of the project.
        project_description (str): Description of the project.
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        Project: XNAT project object.

    Raises:
        AlreadyExistsError: If a project with the same ID already exists in XNAT.
        Exception: If there is an error during the creation of the project.
    """
    xnat_projects_uri = f"{XNAT_URL}/data/projects"

    payload = create_payload_for_project_creation(
        xnat_projects_uri,
        project_id,
        project_secondary_id,
        project_name,
        project_description,
    )

    # Check if project already exists
    for project in get_all_projects(headers):
        if project.ID == project_id:
            raise AlreadyExistsError(
                f"Project ID '{project_id}' already exists. Can't create a new project with the same ID."
            )

    # Create the XNAT project
    response = requests.post(
        xnat_projects_uri,
        headers={**headers, "Content-Type": "application/xml"},
        data=payload,
    )

    if response.status_code == 200:
        logger.info(f"Project '{project_id}' created successfully.")
        return get_project(project_id, headers)
    else:
        raise Exception(f"Error: XNAT project creation failed: {response.status_code} - {response.text}")


def to_create_project(imaging_project: CentralHubProject) -> CreateProject:
    """
    Maps Central Hub project information to XNAT project input to make a request to create a project.

    Args:
        imaging_project (imaging_api.routers.schemas.CentralHubProject): Central Hub project object.

    Returns:
        CreateProject: XNAT create project request object.
    """
    return CreateProject(
        id=str(uuid.uuid4()),
        secondary_id=str(imaging_project.project_id),
        name=f"{imaging_project.project_name}:{imaging_project.project_id}-FL-Project",
        description=f"Project corresponding to central hub project {imaging_project.project_id}",
    )


def set_project_prearchive_settings(project_id: str, headers: dict[str, str]) -> None:
    """
    Set Project Prearchive Settings.
    See also https://wiki.xnat.org/xnat-api/prearchive-api#PrearchiveAPI-SetProjectPrearchiveSettings

    Args:
        project_id (str): Unique identifier for the project
        headers (dict[str, str]): XNAT authentication headers

    Returns:
        None

    Raises:
        Exception: If there is an error during the process of setting the project prearchive settings.
    """
    response = requests.put(
        f"{XNAT_URL}/data/projects/{project_id}/prearchive_code/{ProjectPreArchiveSettings.SEND_ALL_TO_ARCHIVE_AND_IGNORE_EXISTING}",
        headers=headers,
    )
    if response.status_code == 200:
        logger.info(f"Project prearchive settings set for project '{project_id}'")
    else:
        raise Exception(
            f"Error: XNAT Setting project prearchive settings failed: {response.status_code} - {response.text}"
        )


def enable_project_command(project_id: str, container: str, headers: dict[str, str]) -> None:
    """
    Enables a command for a specific project in XNAT.

    Args:
        project_id (str): Unique identifier for the project
        container (str): Name of the command container. For example, for dcm2niix command, "xnat/dcm2niix:latest".
        headers (dict[str, str]): XNAT authentication headers

    Returns:
        None

    Raises:
        Exception: If there is an error during the process of enabling the command for the project.
    """
    container_name_formatted = urllib.parse.quote(container)
    response = requests.get(f"{XNAT_URL}/xapi/commands?image={container_name_formatted}", headers=headers)
    if response.status_code != 200:
        raise Exception(f"Error: XNAT command fetch failed: {response.status_code} - {response.text}")

    # Now use put request to enable the dcm2niix command for the project
    command = response.json()[0]
    command_id = command["id"]
    command_xnat_name = command["xnat"][0]["name"]

    response = requests.put(
        f"{XNAT_URL}/xapi/projects/{project_id}/commands/{command_id}/wrappers/{command_xnat_name}/enabled",
        headers=headers,
    )
    if response.status_code == 200:
        logger.info(f"Command '{container}' enabled for project '{project_id}'")
    else:
        raise Exception(f"Error: Enabling XNAT command '{container}' failed: {response.status_code} - {response.text}")


def add_central_hub_users_to_project(
    central_hub_project: CentralHubProject, project_id: str, headers: dict[str, str]
) -> tuple[list[CreatedUser], list[User]]:
    """
    Adds list of central hub users to an imaging project on XNAT.

    Note users that are disabled will not be created or added to the XNAT project.
    TODO reassess this decision.

    Args:
        central_hub_project (imaging_api.routers.schemas.CentralHubProject): Central Hub project object
        project_id (str): Unique identifier for the project
        headers (dict[str, str]): XNAT authentication headers

    Returns:
        tuple[list[imaging_api.routers.schemas.CreatedUser], list[imaging_api.routers.schemas.User]]: List of created
        users and added users.
    """
    created_users: list[CreatedUser] = []
    added_users: list[User] = []

    if not central_hub_project.users:
        logger.info("No users provided to add to project.")
        return created_users, added_users

    for central_hub_user in central_hub_project.users:
        # If central hub user is disabled, do not attempt to create account.
        if central_hub_user.is_disabled:
            logger.info(
                "Central Hub user is disabled. "
                "It will not be created on XNAT or added to the imaging project.",
            )
            continue

        # Check if user already exists on XNAT, check by 'email' key
        try:
            user_profile = get_user_profile_by("email", central_hub_user.email, headers)
            logger.info("User '%s' already exists on XNAT", user_profile.username)

        except NotFoundError:
            logger.info("User not found on XNAT. Creating user...")
            # Create user on XNAT from Central Hub user
            created_user, user_profile = create_user_from_central_hub_user(central_hub_user, headers)
            # Append to list of created users
            created_users.append(created_user)

        # Add user to project
        if add_user_to_project(user_profile, project_id, headers):
            added_users.append(user_profile)

    return created_users, added_users


async def delete_queued_import_requests(project_id: str, headers: dict[str, str]) -> bool:
    """
    Deletes queued import requests from PACS for a specific project.

    Does not raise an exception if it fails to delete the queued imports.

    Args:
        project_id (str): Unique identifier for the project
        headers (dict[str, str]): XNAT authentication headers

    Returns:
        bool: True if deletion was successful, False otherwise
    """
    # Delete queued import requests when project is deleted
    async for session in get_session():
        queued_imports = await get_queued_pacs_request_by_project(project_id, session)

    # Create a list with the IDs of the queued import requests
    queued_imports_ids = [queued_import.id for queued_import in queued_imports]

    # Check if there are queued import requests to delete
    if not queued_imports_ids:
        logger.info(
            "No queued import requests found for project %s. Nothing to delete.",
            project_id,
        )
        return False

    # Delete queued import requests
    logger.debug(
        "Deleting queued import requests for project %s: %s",
        project_id,
        queued_imports_ids,
    )

    import_delete_response = requests.post(
        f"{XNAT_URL}/xapi/dqr/import/queue",
        headers=headers,
        json=queued_imports_ids,
    )

    # Check status code and log response
    if import_delete_response.status_code != 200:
        logger.error(
            "Failed to delete all queued XNAT import requests for project %s: %s",
            project_id,
            import_delete_response.text,
        )
        return False
    else:
        logger.debug(
            "Successfully deleted XNAT import requests for project with ID: %s.",
            project_id,
        )
        return True


async def delete_project(project_id: str, headers: dict[str, str]) -> Project:
    """
    Deletes an existing project in XNAT.

    Args:
        project_id (str): Unique identifier for the project
        headers (dict[str, str]): XNAT authentication headers

    Returns:
        Project: XNAT project object
    """
    # Check if project exists
    project = get_project(project_id, headers)

    response = requests.delete(f"{XNAT_URL}/data/projects/{project_id}?removeFiles=true", headers=headers)

    # Check status code and log response
    if response.status_code != 200:
        raise Exception(f"Error: XNAT project deletion failed: {response.status_code} - {response.text}")

    logger.info(f"Project with {project_id=} deleted successfully.")

    # Delete queued import requests from PACS for the project
    await delete_queued_import_requests(project_id, headers)

    return project


def get_subjects(project_id: str, headers: dict[str, str]) -> list[Subject]:
    """
    Retrieves a list of subjects in a specific project in XNAT.

    Args:
        project_id (str): Unique identifier for the project.
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        List[Subject]: List of XNAT subject objects.

    Raises:
        Exception: If there is an error while fetching the subjects from XNAT.
    """
    get_project(project_id, headers)

    response = requests.get(f"{XNAT_URL}/data/projects/{project_id}/subjects", headers=headers)
    subjects = [Subject(**subject) for subject in response.json()["ResultSet"]["Result"]]

    if response.status_code == 200:
        return subjects
    else:
        raise Exception(f"Error: XNAT subjects fetch failed: {response.status_code} - {response.text}")


def get_experiments(project_id: str, headers: dict[str, str]) -> list[Experiment]:
    """
    Fetches all XNAT experiments from a project.

    Args:
        project_id (str): Unique identifier for the project.
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        List[Experiment]: List of XNAT experiment objects.

    Raises:
        Exception: If there is an error while fetching the experiments from XNAT.
    """
    get_project(project_id, headers)

    response = requests.get(f"{XNAT_URL}/data/projects/{project_id}/experiments", headers=headers)
    experiments = [Experiment(**experiment) for experiment in response.json()["ResultSet"]["Result"]]

    if response.status_code == 200:
        return experiments
    else:
        raise Exception(f"Error: XNAT experiments fetch failed: {response.status_code} - {response.text}")


def get_experiment(project_id: str, experiment_id_or_label: str, headers: dict[str, str]) -> dict:
    """
    Fetches a specific XNAT experiment from a project.

    Note the XNAT Experiment API supports getting an experiment by either its label or ID:

        ``GET - /data/projects/{project-id}/experiments/{experiment-label | experiment-id}``

    Args:
        project_id (str): Unique identifier for the project.
        experiment_id_or_label (str): Unique identifier or label for the experiment.
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        dict: XNAT experiment dictionary response

    Raises:
        imaging_api.utils.exceptions.NotFoundError: If the experiment with the given ID or label is not found in the
        project.
        Exception: If there is an error during the fetch process.
    """
    get_project(project_id, headers)

    response = requests.get(
        f"{XNAT_URL}/data/projects/{project_id}/experiments/{experiment_id_or_label}?format=json",
        headers=headers,
    )

    if response.status_code == 200:
        # TODO Could create a schema 'Experiment' for the response - this would return Experiment
        return response.json()
    elif response.status_code == 404:
        raise NotFoundError(
            f"Experiment with ID or label '{experiment_id_or_label}' not found in project '{project_id}'."
        )
    else:
        raise Exception(f"Error: XNAT experiment fetch failed: {response.status_code} - {response.text}")


def get_subject_id_from_experiment_response(experiment_response: dict[str, Any]) -> str:
    """
    Extracts the XNAT subject ID from the XNAT experiment response JSON.

    Args:
        experiment_response (Dict[str, Any]): XNAT experiment response JSON.

    Returns:
        str: Subject ID

    Raises:
        Exception: If there is an error during the parsing of the experiment response JSON.
    """
    try:
        subject_id = experiment_response["items"][0]["data_fields"]["subject_ID"]
        return subject_id
    except Exception as e:
        raise Exception(f"Failed to parse XNAT experiment data JSON: {str(e)}")
