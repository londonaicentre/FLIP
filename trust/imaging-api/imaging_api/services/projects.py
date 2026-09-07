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
import xml.etree.ElementTree as ET
from collections.abc import Callable
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
from imaging_api.utils.exceptions import AlreadyExistsError, NotFoundError, XnatFetchError
from imaging_api.utils.logger import logger

XNAT_URL = get_settings().XNAT_URL

# Register the xnat namespace prefix once at module load. ET.register_namespace
# mutates a process-global mapping; doing it here (rather than per-call) makes
# the global-state nature explicit and avoids re-registering on every project
# creation. The URI matches the value passed to create_payload_for_project_creation
# from create_project, so the serializer emits the historical xmlns:xnat="…" form.
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


# Budget for the name->image listing embedded in the raised message, in CHARACTERS rather than
# items. That string becomes an imaging-api 500 detail, and trust-api truncates the relayed body at
# 1000 characters (see trust_api/utils/http.py) before it reaches the hub as the task's error. An
# item cap cannot hold that bound: a realistic 'dcm2niix' -> 'ghcr.io/londonaicentre/xnat-dcm2niix:
# <pin>' pair is ~66 characters against a ~40-character synthetic one, so ten of the former render
# ~1075 characters and push the remediation sentence past the cut - exactly the outcome a cap is
# here to prevent, and an item cap that passes only on short test fixtures hides it. Fit pairs to
# the budget instead, and log the full listing trust-side where nothing truncates.
_MAX_MESSAGE_CHARS = 950

# Bound on an XNAT error body interpolated into a raised message. XNAT answers an unauthenticated
# /xapi call with a Tomcat error page whose entire signal is its <title> (~84 characters); the
# remaining ~600 are a CSS block and boilerplate that would eat most of the budget above. The full
# body goes to the log instead.
_MAX_BODY_CHARS = 200

# The unfiltered re-query runs only once the real error is already in hand, so a hang there delays a
# failure that is already determined. Explicit here for that reason; a file-wide convention for the
# other calls is a separate change.
_DIAGNOSTIC_TIMEOUT_SECONDS = 10


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


def _sorted_command_pairs(commands: list[dict[str, Any]]) -> list[str]:
    """Renders each registered command as a ``'name' -> 'image'`` pair, name-ordered.

    Args:
        commands (list[dict[str, Any]]): Command objects as returned by ``/xapi/commands``.

    Returns:
        list[str]: One rendered pair per command, ordered by name then image.
    """
    return [
        f"{c.get('name', '?')!r} -> {c.get('image', '?')!r}"
        for c in sorted(commands, key=lambda c: (c.get("name") or "", c.get("image") or ""))
    ]


def _render_listing(kept: list[str], omitted: int) -> str:
    """Renders the listing fragment of a message, naming whatever did not fit.

    Args:
        kept (list[str]): Rendered pairs that fit inside the budget.
        omitted (int): How many further pairs were dropped.

    Returns:
        str: The comma-joined pairs with an "and N more" tail, or a pointer to the log when the
        budget could not accommodate even one pair.
    """
    if not kept:
        return f"{omitted} command(s), too long to list here - see the imaging-api log"
    listed = ", ".join(kept)
    if omitted:
        listed += f", and {omitted} more"
    return listed


def _fit_listing(render: Callable[[str], str], pairs: list[str]) -> str:
    """Renders a message with as many of ``pairs`` as fit inside ``_MAX_MESSAGE_CHARS``.

    Appends pairs one at a time and stops on the first that would put the *rendered message* over
    budget, so the bound holds against real image names rather than short ones. The remediation
    sentence lives in the fixed prose, so keeping the whole message under the truncation is what
    keeps the actionable half of it alive.

    Args:
        render (Callable[[str], str]): Builds the full message from a listing fragment.
        pairs (list[str]): Rendered ``'name' -> 'image'`` pairs, most relevant first.

    Returns:
        str: The rendered message, trimmed to the budget.
    """
    kept: list[str] = []
    for pair in pairs:
        candidate = [*kept, pair]
        if len(render(_render_listing(candidate, len(pairs) - len(candidate)))) > _MAX_MESSAGE_CHARS:
            break
        kept = candidate
    return render(_render_listing(kept, len(pairs) - len(kept)))


def _listing_failure_message(container: str, listing_failure: str, status: int | None) -> str:
    """Explains a failure to *obtain* the command listing, by what the status actually implies.

    401 and 403 reach here with different documented causes and must not be collapsed: 401 is a
    credential or session fault, while Container Service 3.7.0+ answers 403 on ``/xapi/commands``
    when the caller lacks the ``ContainerManager`` role (TROUBLESHOOTING 2.3a). Sending an operator
    to re-check working credentials for a 403 is a smaller version of the misdiagnosis FLIP#1093 is
    about.

    Args:
        container (str): Image that was requested and did not match.
        listing_failure (str): Short description of why the listing could not be obtained.
        status (int | None): HTTP status of the diagnostic call, or None for a transport failure.

    Returns:
        str: A message reporting the failure and the remedy its status implies.
    """
    if status == 401:
        remedy = (
            "XNAT answers 401 when the service account's credentials are wrong or its session has "
            "expired - check this trust's XNAT_SERVICE_USER/XNAT_SERVICE_PASSWORD, then retry."
        )
    elif status == 403:
        remedy = (
            "XNAT answers 403 on /xapi/commands when the service account lacks the ContainerManager "
            "role, which Container Service 3.7.0+ requires - grant the role rather than re-checking "
            "credentials (see TROUBLESHOOTING 2.3a), then retry."
        )
    else:
        remedy = (
            "Check the service account's XNAT credentials and its ContainerManager role, and that "
            "/xapi/commands is reachable, then retry."
        )
    return (
        f"No commands found for container '{container}', and the registered commands could not be "
        f"listed to narrow it down ({listing_failure}). This says nothing about whether the XNAT "
        f"Container Service is installed - {remedy}"
    )


def _no_command_message(container: str, headers: dict[str, str]) -> str:
    """Explain why no XNAT command matched ``container``.

    ``get_command_info`` queries ``/xapi/commands?image=<container>``, so an empty
    result means only that no command is registered against *that exact image*. The
    previous message asserted a single cause — a missing Container Service plugin —
    which is the least likely one (FLIP#1093). In practice the plugin is healthy and
    the command is registered against a different image: an XNAT provisioned before
    FLIP#980 still carries ``xnat/dcm2niix:latest``.

    Rather than guess at the cause, list what *is* registered as name→image pairs.
    That is the datum an operator needs and it cannot be wrong: it neither implies
    unrelated containers are stale versions of this one, nor hides a same-named
    command sitting on an older image. Note FLIP#980 changed the repository as well
    as the tag, so comparing repositories would miss the very case this exists for.

    Re-queries XNAT unfiltered on the error path only, and never lets that
    diagnostic call mask the original failure.

    Three outcomes are reported distinctly, because collapsing them is how the
    previous message misdiagnosed. A 200 carrying an empty array is the only one
    that supports the Container-Service hypothesis — and only weakly, since XNAT
    answers a path with no plugin behind it with 404, not an empty list, so a
    readable registry is itself evidence the service is installed. A non-200 or a
    transport error means the listing could not be *obtained*, which establishes
    nothing about the plugin.

    Args:
        container (str): Image that was requested and did not match.
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        str: A message naming what is registered, the registration remedy when the
        registry is readable and empty, or a statement that the listing could not be
        obtained when it could not.
    """
    probe_text = ""
    status: int | None = None
    try:
        probe = requests.get(f"{XNAT_URL}/xapi/commands", headers=headers, timeout=_DIAGNOSTIC_TIMEOUT_SECONDS)
        probe_text = probe.text
        status = probe.status_code
        if probe.status_code != 200:
            listing_failure: str | None = f"HTTP {probe.status_code}"
            registered: Any = []
        else:
            listing_failure = None
            registered = probe.json()
    except Exception as exc:  # diagnostics must never replace the real error
        listing_failure = f"{type(exc).__name__}: {exc}"
        registered = []
        # The raised message carries only a short description and nothing downstream sees the
        # exception, so this is the only trust-side record of what actually failed.
        logger.error(
            "Could not list XNAT commands while diagnosing a lookup miss for image %r (%s)",
            container,
            listing_failure,
            exc_info=True,
        )

    # A 200 whose body is not an array of objects must not crash this helper. The formatting
    # below assumes mappings, and `sorted()` over a dict yields its string keys, so `c.get(...)`
    # would raise AttributeError out of a function whose whole contract is never to replace the
    # underlying error. Live XNAT answers this endpoint with a JSON array and signals faults with
    # a non-200, so this is defensive: it is reachable through an intermediary that rewrites the
    # body (the Helm chart's nginx, a site proxy), not from XNAT itself. Treat it as "could not
    # list" rather than "nothing registered" - an unparseable body is not evidence of an empty
    # registry.
    if not isinstance(registered, list):
        listing_failure = listing_failure or "unexpected response body"
        registered = []
    else:
        usable = [c for c in registered if isinstance(c, dict)]
        if registered and not usable:
            listing_failure = listing_failure or "unexpected response body"
        registered = usable

    if listing_failure is not None:
        if status is not None:
            # The raised message deliberately omits the body - an XNAT login page is ~683
            # characters of Tomcat boilerplate - so keep it here, where nothing truncates it.
            logger.error(
                "Could not list XNAT commands while diagnosing a lookup miss for image %r: %s; body: %s",
                container,
                listing_failure,
                probe_text,
            )
        return _listing_failure_message(container, listing_failure, status)

    if not registered:
        return (
            f"No commands found for container '{container}': the command registry is readable and "
            "empty, so no command is registered on this XNAT. Re-run configure-dcm2niix.sh (compose) "
            "or the xnat-init job (k8s) to register it, and check the service account holds the "
            "ContainerManager role - Container Service 3.7.0+ requires it, and without it "
            "registration reports success having registered nothing (see TROUBLESHOOTING 2.3a). A "
            "readable registry means the Container Service answered, so a missing plugin is the "
            "least likely cause here; rule it out last."
        )

    pairs = _sorted_command_pairs(registered)
    # Trust-side the full listing is never truncated, so keep it where an operator can read it.
    logger.error(
        "No XNAT command matched image %r; %d command(s) registered: %s",
        container,
        len(registered),
        ", ".join(pairs),
    )

    matching = [c for c in registered if c.get("image") == container]
    if matching:
        # Lead with the matching entries. This branch asserts the exact image *is* registered, and
        # a budget-trimmed listing that dropped it would contradict the very claim it is evidence
        # for - the failure mode this whole helper exists to remove.
        others = [c for c in registered if c.get("image") != container]
        return _fit_listing(
            lambda listed: (
                f"No commands found for container '{container}', yet that exact image is registered: "
                f"{listed}. The filtered lookup (/xapi/commands?image=<image>) returned nothing while "
                "the unfiltered listing shows it, so the registration is not stale and re-registering "
                "the same image will not change anything - suspect the lookup instead: a case or "
                "encoding difference in the image string, an 'image' field carrying a digest, or a "
                "command with no 'xnat' wrapper."
            ),
            _sorted_command_pairs(matching) + _sorted_command_pairs(others),
        )

    return _fit_listing(
        lambda listed: (
            f"No commands found for container '{container}'. The Container Service is installed and "
            f"has {len(registered)} command(s) registered: {listed}. If one of those is the same tool "
            "on an older image, this XNAT's registration predates the current pin - re-run "
            "configure-dcm2niix.sh (compose) or the xnat-init job (k8s) to re-register it, and check "
            "the trust's imaging-api image is current."
        ),
        pairs,
    )


def get_command_info(container: str, headers: dict[str, str]) -> tuple[int, str]:
    """
    Fetches the XNAT command ID and wrapper name for a given container image.

    Args:
        container (str): Container image name (typically the configured ``Settings.DCM2NIIX_IMAGE``).
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        tuple[int, str]: A tuple of (command_id, wrapper_name).

    Raises:
        Exception: If the command cannot be fetched from XNAT, or the command that matches carries
            no ``xnat`` wrapper to launch.
    """
    container_name_formatted = urllib.parse.quote(container)
    response = requests.get(f"{XNAT_URL}/xapi/commands?image={container_name_formatted}", headers=headers)
    if response.status_code != 200:
        # Credentials that are wrong from the start fail here, before the diagnostic helper below is
        # ever reached, and XNAT answers with a whole Tomcat error page. Bound it in the raised
        # message - that string travels to the hub through trust-api's 1000-character truncation -
        # and keep the full body in the log.
        logger.error(
            "XNAT command lookup for image %r failed: HTTP %s; body: %s",
            container,
            response.status_code,
            response.text,
        )
        raise Exception(f"Error: XNAT command fetch failed: {response.status_code} - {_bounded_body(response.text)}")

    commands = response.json()
    if not commands:
        raise Exception(_no_command_message(container, headers))
    command = commands[0]

    # The mismatch message above teaches "a command with no 'xnat' wrapper" as a hypothesis to
    # check. Without this guard that exact state raises a bare KeyError('xnat') - or IndexError on
    # an empty wrapper list - two lines from the text recommending it, and travels to the hub as a
    # task error with nothing in it. 2.3a's verification step checks for the wrapper, so it is a
    # real state rather than a theoretical one.
    wrappers = command.get("xnat")
    wrapper_name = None
    if isinstance(wrappers, list) and wrappers and isinstance(wrappers[0], dict):
        wrapper_name = wrappers[0].get("name")
    if not wrapper_name:
        raise Exception(
            f"XNAT command {command.get('name', '?')!r} (id {command.get('id', '?')}) matches image "
            f"'{container}' but carries no 'xnat' wrapper, so there is no wrapper name to launch. "
            "Re-run configure-dcm2niix.sh (compose) or the xnat-init job (k8s) to re-register it, and "
            "verify with GET /xapi/commands?name=dcm2niix that the entry has a 'dcm2niix-scan' "
            "wrapper (see TROUBLESHOOTING 2.3a)."
        )
    return command["id"], wrapper_name


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
    command_id, wrapper_name = get_command_info(container, headers)

    # Enable the command at the project level — required by XNAT to validate the action key
    # in project-scoped event subscriptions
    response = requests.put(
        f"{XNAT_URL}/xapi/projects/{project_id}/commands/{command_id}/wrappers/{wrapper_name}/enabled",
        headers=headers,
    )
    if response.status_code != 200:
        raise Exception(
            f"Error: Enabling command '{container}' for project '{project_id}' failed: "
            f"{response.status_code} - {response.text}"
        )

    subscription_payload = {
        "name": "DICOM-NIfTI Conversion",
        "event-selector": "org.nrg.xnat.eventservice.events.ScanEvent:CREATED",
        "action-key": f"org.nrg.containers.services.CommandActionProvider:{command_id}",
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
        f"{XNAT_URL}/xapi/projects/{project_id}/events/subscription",
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
                "Central Hub user is disabled. It will not be created on XNAT or added to the imaging project.",
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

    Raises:
        imaging_api.utils.exceptions.NotFoundError: If the project does not exist on XNAT.
        Exception: If XNAT returns a non-200 response for the delete call.
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
        list[Subject]: List of XNAT subject objects.

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
    response = requests.get(f"{XNAT_URL}/data/experiments", params={"project": project_id}, headers=headers)

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
