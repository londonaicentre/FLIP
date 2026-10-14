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

import uuid
import xml.etree.ElementTree as ET
from typing import Any, NamedTuple

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
from imaging_api.services.users import create_user_from_central_hub_user, get_user_profile_by, issue_invite
from imaging_api.utils.enums import ProjectPreArchiveSettings
from imaging_api.utils.exceptions import AlreadyExistsError, NotFoundError, XnatFetchError
from imaging_api.utils.logger import logger
from imaging_api.utils.xnat_url import xnat_url

XNAT_URL = get_settings().XNAT_URL

# Register the xnat namespace prefix once at module load. ET.register_namespace
# mutates a process-global mapping; doing it here (rather than per-call) makes
# the global-state nature explicit and avoids re-registering on every project
# creation. The URI matches the value passed to create_payload_for_project_creation
# from create_project, so the serializer emits the historical xmlns:xnat="…" form. It is a namespace
# URI rather than a request, the one f-string over XNAT_URL that tests/utils/test_no_raw_xnat_urls.py
# exempts (#1386).
ET.register_namespace("xnat", f"{XNAT_URL}/data/projects")


def get_project_from_central_hub_project_id(central_hub_project_id: str, headers: dict[str, str]) -> Project:
    """
    Gets the XNAT project from a central hub project ID (corresponds to XNAT project secondary ID)

    Args:
        central_hub_project_id (str): Central hub project ID
        headers (dict[str, str]): XNAT authentication headers

    Returns:
        Project: XNAT project object

    Raises:
        imaging_api.utils.exceptions.NotFoundError: If no XNAT project has a matching ``secondary_ID``.
        Exception: If the upstream ``get_all_projects`` call fails.
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

    Raises:
        imaging_api.utils.exceptions.NotFoundError: If no XNAT project matches ``project_id``.
        Exception: If the upstream ``get_all_projects`` call fails.
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
        list[Project]: List of XNAT project objects

    Raises:
        Exception: If the HTTP request to XNAT fails, or if XNAT returns a non-200 response.
    """
    try:
        response = requests.get(xnat_url("data", "projects"), headers=headers)
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

    Builds the XML using ``xml.etree.ElementTree`` so that XML control characters
    (``<``, ``>``, ``&``, ``"``, ``'``) in any field are escaped as entity
    references rather than interpolated raw — this defeats XML injection that
    could otherwise mutate the projectData document sent to XNAT.

    Args:
        xnat_projects_uri (str): XNAT projects URI.
        project_id (str): Unique identifier for the project.
        project_secondary_id (str): Secondary ID for the project.
        project_name (str): Name of the project.
        project_description (str, optional): Description of the project.

    Returns:
        str: XML payload for creating the project.
    """
    root = ET.Element(f"{{{xnat_projects_uri}}}projectData")
    ET.SubElement(root, "ID").text = project_id
    ET.SubElement(root, "secondary_ID").text = project_secondary_id
    ET.SubElement(root, "name").text = project_name
    ET.SubElement(root, "description").text = project_description
    return ET.tostring(root, encoding="unicode", short_empty_elements=False)


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
    xnat_projects_uri = xnat_url("data", "projects")

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
        xnat_url(
            "data",
            "projects",
            project_id,
            "prearchive_code",
            str(ProjectPreArchiveSettings.SEND_ALL_TO_ARCHIVE_AND_IGNORE_EXISTING.value),
        ),
        headers=headers,
    )
    if response.status_code == 200:
        logger.info(f"Project prearchive settings set for project '{project_id}'")
    else:
        raise Exception(
            f"Error: XNAT Setting project prearchive settings failed: {response.status_code} - {response.text}"
        )


# Bound on an XNAT error body interpolated into a raised message. These messages become an
# imaging-api 500 detail, and trust-api truncates the relayed body at 1000 characters (see
# trust_api/utils/http.py) before it reaches the hub as the task's error. XNAT answers an
# unauthenticated /xapi call with a Tomcat error page whose entire signal is its <title> (~84
# characters); the remaining ~600 are a CSS block and boilerplate. The full body goes to the log.
_MAX_BODY_CHARS = 200


def _bounded_body(text: str) -> str:
    """Trims an XNAT error body to its informative head for embedding in a raised message.

    Args:
        text (str): Raw response body from XNAT.

    Returns:
        str: The body unchanged when it is already short, otherwise its first ``_MAX_BODY_CHARS``
        characters with a pointer to the trust-side log for the remainder.
    """
    body = (text or "").strip()
    if len(body) <= _MAX_BODY_CHARS:
        return body
    omitted = len(body) - _MAX_BODY_CHARS
    return f"{body[:_MAX_BODY_CHARS]}... (+{omitted} more characters, full body in the imaging-api log)"


class CommandInfo(NamedTuple):
    """The dcm2niix wrapper a project's DICOM->NIfTI trigger launches, and the command it belongs to.

    Attributes:
        command_id (int): The command's id, which the per-project enable URL names.
        wrapper_name (str): The wrapper's name (``DCM2NIIX_WRAPPER_NAME``).
        wrapper_id (int): The wrapper's id. An event subscription's action key names the wrapper,
            not the command (container-service 3.8.1 ``CommandActionProvider``), and the two ids
            only coincide on an XNAT that never registered another command.
    """

    command_id: int
    wrapper_name: str
    wrapper_id: int


# The name of every DICOM->NIfTI subscription this service creates. configure-dcm2niix.sh finds
# FLIP's subscriptions by it to re-point them at the current dcm2niix wrapper (FLIP#1451);
# trust/xnat/tests/test_dcm2niix_registration.py holds the two equal.
DCM2NIIX_SUBSCRIPTION_NAME = "DICOM-NIfTI Conversion"
# The wrapper a project's trigger launches, and the XNAT data type it runs on: the `xnat[0]` entry of
# trust/xnat/xnat/config/dcm2niix_command.json, which that same test holds these to.
DCM2NIIX_WRAPPER_NAME = "dcm2niix-scan"
DCM2NIIX_CONTEXT = "xnat:imageScanData"

_RERUN_CONFIGURE = (
    "Re-run configure-dcm2niix.sh (compose) or the xnat-init job (k8s), which updates the command in place "
    "from dcm2niix_command.json"
)


def _listing_failed(project_id: str, status: int, text: str) -> str:
    """Explains a commands/available listing XNAT refused, by what its status implies."""
    if status == 401:
        remedy = (
            "XNAT answers 401 when the service account's credentials are wrong or its session has expired - "
            "check this trust's XNAT_SERVICE_USER/XNAT_SERVICE_PASSWORD, then retry."
        )
    elif status == 403:
        remedy = "XNAT answers 403 when the service account may not read the Container Service - check its roles."
    else:
        remedy = "Check that XNAT and its Container Service are up, then retry."
    return (
        f"Error: listing the Container Service commands for project '{project_id}''s DICOM->NIfTI trigger "
        f"failed: {status} - {_bounded_body(text)}. {remedy}"
    )


def get_wrapper_action_key(wrapper_id: int, wrapper_name: str, headers: dict[str, str]) -> str:
    """Returns the event-service action key XNAT offers for a Container Service wrapper.

    Taken from XNAT rather than formatted here. ``GET /xapi/events/allactions`` (any authenticated
    user) lists one action per command wrapper, named after the wrapper, with the key
    ``<provider class>:<wrapper id>`` (xnat-web 1.10.0 EventServiceRestApi.java:383-387;
    container-service 3.8.1 CommandActionProvider.java:389-403, MultiActionProvider.java:31-32). The
    one entry with this wrapper's name and id is the key a subscription must carry to be valid.

    Args:
        wrapper_id (int): The wrapper's id, from ``get_command_info``.
        wrapper_name (str): The wrapper's name, from ``get_command_info``.
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        str: The action key, e.g. ``org.nrg.containers.services.CommandActionProvider:5``.

    Raises:
        Exception: If the listing fails, or does not hold exactly one action for the wrapper.
    """
    response = requests.get(xnat_url("xapi", "events", "allactions"), headers=headers)
    if response.status_code != 200:
        raise Exception(
            f"Error: XNAT event-service action listing failed: {response.status_code} - {_bounded_body(response.text)}"
        )
    actions = response.json() or []
    keys = [
        action["action-key"]
        for action in actions
        if isinstance(action, dict)
        and action.get("display-name") == wrapper_name
        and isinstance(action.get("action-key"), str)
        and action["action-key"].rsplit(":", 1)[-1] == str(wrapper_id)
    ]
    if len(keys) != 1:
        raise Exception(
            f"XNAT lists {len(keys)} event-service actions for wrapper {wrapper_name!r} (id {wrapper_id}), "
            "expected exactly one, so no DICOM->NIfTI trigger can be created for it."
        )
    return str(keys[0])


def get_command_info(project_id: str, container: str, headers: dict[str, str]) -> CommandInfo:
    """Finds the dcm2niix wrapper a project's trigger launches, and checks it runs the pinned image.

    ``GET /xapi/commands/available/site?xsiType=xnat:imageScanData`` (container-service 3.8.1
    CommandRestApi.java:219-240, CommandServiceImpl.java:433-477) answers with one row per wrapper of
    every command whose context matches, carrying the command id, the wrapper id and name, the
    command's image, and ``enabled`` for the site. One row answers both questions the old
    ``/xapi/commands?image=`` lookup could not: whether the dcm2niix wrapper exists at all, and which
    image it actually runs, so a wrapper on another image (an XNAT whose registration predates the
    current pin) is named with both images in hand.

    The site listing, not the project's (``/xapi/projects/{project}/commands/available``): the
    project listing filters each wrapper by the caller's permissions in the project
    (ContainerServicePermissionUtils.java:116-128), and for a project imaging-api has just created it
    came back empty for the service account on dev trust 1 (14 Oct), while it listed the wrapper for
    the trust's older projects. This lookup runs straight after project creation, so it cannot rely
    on that.

    Args:
        project_id (str): XNAT project ID the trigger is for, named in the messages.
        container (str): The image the wrapper must run (typically ``Settings.DCM2NIIX_IMAGE``).
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        CommandInfo: The command id, and the wrapper's name and id.

    Raises:
        Exception: If the listing fails, holds no dcm2niix wrapper, holds more than one, or the one it
            holds runs another image. Every message stays well inside the 1000 characters trust-api
            relays to the hub.
    """
    response = requests.get(
        xnat_url("xapi", "commands", "available", "site", query={"xsiType": DCM2NIIX_CONTEXT}),
        headers=headers,
    )
    if response.status_code != 200:
        logger.error(
            "Listing the Container Service commands for project %r failed: HTTP %s; body: %s",
            project_id,
            response.status_code,
            response.text,
        )
        raise Exception(_listing_failed(project_id, response.status_code, response.text))
    listing = response.json()
    if not isinstance(listing, list):
        logger.error("Unexpected commands/available/site body for project %r: %s", project_id, response.text)
        raise Exception(
            f"Error: XNAT's Container Service command listing came back as {type(listing).__name__}, not a "
            f"list, so project '{project_id}' gets no DICOM->NIfTI trigger - check what answers for XNAT."
        )

    rows = [row for row in listing if isinstance(row, dict) and row.get("wrapper-name") == DCM2NIIX_WRAPPER_NAME]
    if not rows:
        logger.error("No %r wrapper among XNAT's Container Service commands: %s", DCM2NIIX_WRAPPER_NAME, listing)
        raise Exception(
            f"No '{DCM2NIIX_WRAPPER_NAME}' wrapper is registered for scans on this XNAT, so project "
            f"'{project_id}' gets no DICOM->NIfTI trigger. {_RERUN_CONFIGURE}, and check the admin holds "
            "ContainerManager (TROUBLESHOOTING 2.3a): without it registration reports success having "
            "registered nothing. GET /xapi/commands?name=dcm2niix shows what is registered."
        )

    pinned = [row for row in rows if row.get("image-name") == container]
    if len(rows) == 1 and not pinned:
        (row,) = rows
        raise Exception(
            f"XNAT's '{DCM2NIIX_WRAPPER_NAME}' wrapper (command {row.get('command-id', '?')}) runs "
            f"'{row.get('image-name', '?')}', but this imaging-api expects '{container}', so a trigger would "
            f"convert with the wrong image. {_RERUN_CONFIGURE} to the pin (TROUBLESHOOTING 2.3c), and check the "
            "trust's imaging-api image is current."
        )
    if len(pinned) != 1:
        described = ", ".join(
            f"command {row.get('command-id', '?')} on {row.get('image-name', '?')!r}" for row in rows[:5]
        )
        raise Exception(
            f"{len(rows)} '{DCM2NIIX_WRAPPER_NAME}' wrappers are registered on this XNAT ({described}), and "
            f"{len(pinned)} run the pinned '{container}', so which one project '{project_id}''s trigger "
            "should launch is ambiguous. Delete the dcm2niix command no subscription names, then re-run "
            "configure-dcm2niix.sh (compose) or the xnat-init job (k8s)."
        )

    (row,) = pinned
    # A wrapper the site has not enabled cannot be enabled for a project, and XNAT would refuse the
    # trigger as invalid; the configure step enables it, so its absence means that step did not finish.
    if row.get("enabled") is False:
        raise Exception(
            f"XNAT's '{DCM2NIIX_WRAPPER_NAME}' wrapper (command {row.get('command-id', '?')}) is not enabled "
            f"site-wide, so project '{project_id}''s DICOM->NIfTI trigger would be invalid. {_RERUN_CONFIGURE}, "
            "which enables it."
        )
    command_id, wrapper_id = row.get("command-id"), row.get("wrapper-id")
    # The subscription's action key names the wrapper id, so a guess here would create a trigger XNAT
    # reports invalid. XNAT always returns both; refuse rather than fall back to the command id.
    if type(command_id) is not int or type(wrapper_id) is not int:
        raise Exception(
            f"XNAT's '{DCM2NIIX_WRAPPER_NAME}' wrapper came back without numeric ids "
            f"(command {command_id!r}, wrapper {wrapper_id!r}), so no DICOM->NIfTI trigger can name it."
        )
    return CommandInfo(command_id=command_id, wrapper_name=DCM2NIIX_WRAPPER_NAME, wrapper_id=wrapper_id)


def create_project_event_subscription(project_id: str, container: str, active: bool, headers: dict[str, str]) -> None:
    """
    Creates a project-scoped event subscription in XNAT that auto-triggers a command on scan upload.

    The subscription listens for ScanEvent:CREATED events within the specified project and triggers
    the given container command when a scan with DICOM resources is created. The active flag controls
    whether the subscription is enabled or deactivated on creation.

    Args:
        project_id (str): XNAT project ID to scope the subscription to.
        container (str): Container image name (typically the configured ``Settings.DCM2NIIX_IMAGE``).
        headers (dict[str, str]): XNAT authentication headers.
        active (bool): If True, the subscription is active immediately. If False, it is created
            but deactivated (can be toggled later via the XNAT API).

    Raises:
        Exception: If the subscription creation fails.
    """
    # Looked up first: the project enable below needs the command id it returns.
    command_id, wrapper_name, wrapper_id = get_command_info(project_id, container, headers)

    # Enable the command at the project level — required by XNAT to validate the action key
    # in project-scoped event subscriptions
    response = requests.put(
        xnat_url("xapi", "projects", project_id, "commands", str(command_id), "wrappers", wrapper_name, "enabled"),
        headers=headers,
    )
    if response.status_code != 200:
        raise Exception(
            f"Error: Enabling command '{container}' for project '{project_id}' failed: "
            f"{response.status_code} - {response.text}"
        )

    # The key names the WRAPPER: container-service 3.8.1 parses its number as a wrapper id
    # (CommandActionProvider.java:102,111) and validates it with wrapperExists and the project's
    # enablement of that wrapper (:476-489). This used to format the command id into it, which only
    # worked while the two happened to be equal (FLIP#1451).
    action_key = get_wrapper_action_key(wrapper_id, wrapper_name, headers)

    subscription_payload = {
        "name": DCM2NIIX_SUBSCRIPTION_NAME,
        "event-selector": "org.nrg.xnat.eventservice.events.ScanEvent:CREATED",
        "action-key": action_key,
        "attributes": {},
        "active": active,
        "event-filter": {
            "event-type": "org.nrg.xnat.eventservice.events.ScanEvent",
            "status": "CREATED",
            "project-ids": [project_id],
            "payload-filter": '(@.resources.length() > 0 && "DICOM" in @.resources[*].label)',
        },
        "act-as-event-user": False,
    }

    response = requests.post(
        xnat_url("xapi", "projects", project_id, "events", "subscription"),
        headers=headers,
        json=subscription_payload,
    )
    if response.status_code in (200, 201):
        state = "active" if active else "inactive"
        logger.info(f"Event subscription for '{container}' created ({state}) for project '{project_id}'")
    else:
        raise Exception(
            f"Error: Creating event subscription for '{container}' failed: {response.status_code} - {response.text}"
        )


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
        tuple[list[imaging_api.routers.schemas.CreatedUser], list[imaging_api.routers.schemas.User]]: The users to
        invite (created this run, or existing but never logged in — each with a sealed set-password path) and the
        users to notify of project access (existing accounts that have logged in before). A user appears in exactly
        one of the two, so the hub sends either the invite or the added-to-project notice, never both.
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
                "Central Hub user is disabled. It will not be created on XNAT or added to the imaging project.",
            )
            continue

        # Check if user already exists on XNAT, check by 'email' key
        try:
            user_profile = get_user_profile_by("email", central_hub_user.email, headers)
        except NotFoundError:
            logger.info("User not found on XNAT. Creating user...")
            # Create user on XNAT from Central Hub user
            created_user, user_profile = create_user_from_central_hub_user(central_hub_user, headers)
            # Append to list of created users
            created_users.append(created_user)
            invited = True
        else:
            # An account that exists but has never authenticated has no password its owner knows —
            # the first invite was lost, expired unused, or its token issuance failed after the
            # account had been created. Re-issue the invite rather than sending the added-to-project
            # notice, which tells the recipient to log in with credentials they never set.
            if user_profile.lastSuccessfulLogin is None:
                logger.info(
                    "User '%s' already exists on XNAT but has never logged in. Re-issuing the set-password invite...",
                    user_profile.username,
                )
                created_users.append(issue_invite(user_profile, headers))
                invited = True
            else:
                logger.info("User '%s' already exists on XNAT", user_profile.username)
                invited = False

        # Add user to project. Only a user who was NOT invited this run gets the added-to-project
        # notice: the invite already names the project, and the notice's "log in with your existing
        # credentials" is untrue for an account whose password has never been set.
        if add_user_to_project(user_profile, project_id, headers) and not invited:
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
        xnat_url("xapi", "dqr", "import", "queue"),
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

    Raises:
        imaging_api.utils.exceptions.NotFoundError: If the project does not exist on XNAT.
        Exception: If XNAT returns a non-200 response for the delete call.
    """
    # Check if project exists
    project = get_project(project_id, headers)

    response = requests.delete(xnat_url("data", "projects", project_id, query={"removeFiles": "true"}), headers=headers)

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
        list[Subject]: List of XNAT subject objects.

    Raises:
        Exception: If there is an error while fetching the subjects from XNAT.
    """
    get_project(project_id, headers)

    response = requests.get(xnat_url("data", "projects", project_id, "subjects"), headers=headers)
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
        list[Experiment]: List of XNAT experiment objects.

    Raises:
        imaging_api.utils.exceptions.XnatFetchError: If XNAT returns a non-200 response for the experiments listing.
    """
    get_project(project_id, headers)

    # Use the GLOBAL experiments listing filtered by project, NOT the project-scoped
    # /data/projects/{id}/experiments. The project-scoped listing is filtered by per-data-type
    # element security, so sessions whose modality is not registered there (e.g.
    # xnat:dxSessionData for chest X-rays) are silently omitted — making the import look stuck
    # at "0 imported". The global listing returns identical fields without that filter.
    response = requests.get(xnat_url("data", "experiments"), params={"project": project_id}, headers=headers)

    # Check the status before parsing: a non-200 XNAT response carries an HTML/plain-text body, so
    # parsing it as JSON first would raise and mask the real HTTP status.
    if response.status_code != 200:
        raise XnatFetchError(f"Error: XNAT experiments fetch failed: {response.status_code} - {response.text}")

    return [Experiment(**experiment) for experiment in response.json()["ResultSet"]["Result"]]


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
        ValueError: If a project or experiment identifier is empty or a dot-segment.
        imaging_api.utils.exceptions.NotFoundError: If the experiment with the given ID or label is not found in the
        project.
        Exception: If there is an error during the fetch process.
    """
    url = xnat_url("data", "projects", project_id, "experiments", experiment_id_or_label, query={"format": "json"})
    get_project(project_id, headers)

    response = requests.get(
        url,
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
        experiment_response (dict[str, Any]): XNAT experiment response JSON.

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
