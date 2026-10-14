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

from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
import requests

from imaging_api.config import get_settings
from imaging_api.db.models import QueuedPacsRequest
from imaging_api.routers.schemas import CentralHubProject, CentralHubUser, CreatedUser, Project, User
from imaging_api.services.projects import (
    add_central_hub_users_to_project,
    create_payload_for_project_creation,
    create_project,
    create_project_event_subscription,
    delete_project,
    delete_queued_import_requests,
    get_all_projects,
    get_command_info,
    get_experiment,
    get_experiments,
    get_project,
    get_project_from_central_hub_project_id,
    get_subject_id_from_experiment_response,
    get_subjects,
    get_wrapper_action_key,
    set_project_prearchive_settings,
    to_create_project,
)
from imaging_api.utils.exceptions import AlreadyExistsError, NotFoundError, XnatFetchError


@pytest.fixture
def headers():
    return {}


_PROJECT_DICT = {
    "ID": "TEST",
    "secondary_ID": "SEC1",
    "name": "Test Project",
    "description": "A test project",
    "pi_firstname": "John",
    "pi_lastname": "Doe",
    "URI": "/projects/TEST",
}

_USER_DICT = {
    "lastModified": 123,
    "username": "alice",
    "enabled": True,
    "id": 1,
    "secured": False,
    "email": "alice@test.com",
    "verified": True,
    "firstName": "Alice",
    "lastName": "A",
}


# ===========================================================================
# get_all_projects
# ===========================================================================
@patch("imaging_api.services.projects.requests.get")
def test_get_all_projects_success(mock_get, headers):
    mock_get.return_value = MagicMock(
        status_code=200,
        json=MagicMock(return_value={"ResultSet": {"Result": [_PROJECT_DICT]}}),
    )
    projects = get_all_projects(headers)
    assert len(projects) == 1
    assert projects[0].ID == "TEST"


@patch("imaging_api.services.projects.requests.get")
def test_get_all_projects_failure(mock_get, headers):
    mock_get.return_value = MagicMock(status_code=500, text="Server Error")
    with pytest.raises(Exception, match="XNAT projects fetch failed"):
        get_all_projects(headers)


@patch("imaging_api.services.projects.requests.get")
def test_get_all_projects_connection_error(mock_get, headers):
    mock_get.side_effect = ConnectionError("refused")
    with pytest.raises(Exception, match="XNAT projects fetch failed"):
        get_all_projects(headers)


# ===========================================================================
# get_project
# ===========================================================================
@patch("imaging_api.services.projects.get_all_projects")
def test_get_project_success(mock_get_all, headers):
    mock_get_all.return_value = [Project(**_PROJECT_DICT)]
    project = get_project("TEST", headers)
    assert project.ID == "TEST"


@patch("imaging_api.services.projects.get_all_projects")
def test_get_project_not_found(mock_get_all, headers):
    mock_get_all.return_value = []
    with pytest.raises(NotFoundError, match="not found"):
        get_project("MISSING", headers)


@patch("imaging_api.services.projects.get_all_projects")
def test_get_project_fetch_error(mock_get_all, headers):
    mock_get_all.side_effect = Exception("connection refused")
    with pytest.raises(Exception, match="XNAT project fetch failed"):
        get_project("TEST", headers)


# ===========================================================================
# get_project_from_central_hub_project_id
# ===========================================================================
@patch("imaging_api.services.projects.get_all_projects")
def test_get_project_from_central_hub_id_success(mock_get_all, headers):
    mock_get_all.return_value = [Project(**_PROJECT_DICT)]
    project = get_project_from_central_hub_project_id("SEC1", headers)
    assert project.secondary_ID == "SEC1"


@patch("imaging_api.services.projects.get_all_projects")
def test_get_project_from_central_hub_id_not_found(mock_get_all, headers):
    mock_get_all.return_value = []
    with pytest.raises(NotFoundError, match="not found"):
        get_project_from_central_hub_project_id("MISSING", headers)


@patch("imaging_api.services.projects.get_all_projects")
def test_get_project_from_central_hub_id_fetch_error(mock_get_all, headers):
    mock_get_all.side_effect = Exception("connection refused")
    with pytest.raises(Exception, match="XNAT project fetch failed"):
        get_project_from_central_hub_project_id("SEC1", headers)


# ===========================================================================
# create_payload_for_project_creation
# ===========================================================================
def test_create_payload_for_project_creation():
    payload = create_payload_for_project_creation(
        "http://xnat/projects",
        "P1",
        "S1",
        "My Project",
        "A description",
    )
    assert "<ID>P1</ID>" in payload
    assert "<secondary_ID>S1</secondary_ID>" in payload
    assert "<name>My Project</name>" in payload
    assert "<description>A description</description>" in payload


def test_create_payload_for_project_creation_escapes_xml_control_chars():
    """
    name/description must be XML-escaped, never interpolated raw, so an
    attacker-supplied value cannot inject elements into the projectData
    document XNAT receives.
    """
    import xml.etree.ElementTree as ET

    payload = create_payload_for_project_creation(
        "http://xnat/projects",
        "P1",
        "S1",
        "evil</name><name>injected",
        "less < and & ampersand",
    )

    # Raw injection markers must be absent, replaced by entity references.
    assert "</name><name>injected" not in payload
    assert "&lt;/name&gt;&lt;name&gt;injected" in payload
    assert "&lt;" in payload
    assert "&amp;" in payload

    # The payload still parses as a single projectData element with the
    # attacker's value carried verbatim as text — never as markup. Children
    # are in no namespace (only the root carries the xnat prefix).
    root = ET.fromstring(payload)
    name_elements = root.findall("name")
    assert len(name_elements) == 1
    assert name_elements[0].text == "evil</name><name>injected"


def test_create_payload_for_project_creation_blocks_xxe_doctype():
    """
    XNAT-side payload must never carry a DOCTYPE/ENTITY block. ElementTree's
    serializer never emits one, so attacker-controlled fields can't smuggle XXE
    even when they look like a DOCTYPE declaration.
    """
    attacker_value = '<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
    payload = create_payload_for_project_creation(
        "http://xnat/projects",
        "P1",
        "S1",
        attacker_value,
        "",
    )
    # The serializer must never emit DOCTYPE/ENTITY markup tokens — they would
    # only appear if the attacker value were interpolated raw.
    assert "<!DOCTYPE" not in payload
    assert "<!ENTITY" not in payload
    # The attacker value survives only as escaped text, never as markup.
    assert "&lt;!DOCTYPE" in payload


# ===========================================================================
# create_project
# ===========================================================================
@patch("imaging_api.services.projects.get_project")
@patch("imaging_api.services.projects.get_all_projects")
@patch("imaging_api.services.projects.requests.post")
def test_create_project_success(mock_post, mock_get_all, mock_get_project, headers):
    mock_post.return_value = MagicMock(status_code=200)
    mock_get_all.return_value = []
    mock_get_project.return_value = Project(**_PROJECT_DICT)

    project = create_project("TEST", "SEC1", "Test Project", "desc", headers)
    assert project.ID == "TEST"


@patch("imaging_api.services.projects.get_all_projects")
def test_create_project_already_exists(mock_get_all, headers):
    mock_get_all.return_value = [Project(**_PROJECT_DICT)]

    with pytest.raises(AlreadyExistsError, match="already exists"):
        create_project("TEST", "SEC1", "Test Project", "desc", headers)


@patch("imaging_api.services.projects.get_all_projects")
@patch("imaging_api.services.projects.requests.post")
def test_create_project_post_failure(mock_post, mock_get_all, headers):
    mock_get_all.return_value = []
    mock_post.return_value = MagicMock(status_code=500, text="Server Error")

    with pytest.raises(Exception, match="XNAT project creation failed"):
        create_project("NEW", "SEC2", "New Project", "desc", headers)


# ===========================================================================
# to_create_project
# ===========================================================================
def test_to_create_project():
    hub_project = CentralHubProject(
        project_id=uuid4(),
        trust_id=uuid4(),
        project_name="My Project",
        query="SELECT *",
    )
    create_req = to_create_project(hub_project)
    assert str(hub_project.project_id) in create_req.name
    assert create_req.secondary_id == str(hub_project.project_id)


# ===========================================================================
# set_project_prearchive_settings
# ===========================================================================
@patch("imaging_api.services.projects.requests.put")
def test_set_project_prearchive_settings_success(mock_put, headers):
    mock_put.return_value = MagicMock(status_code=200)
    set_project_prearchive_settings("TEST", headers)  # should not raise


@patch("imaging_api.services.projects.requests.put")
def test_set_project_prearchive_settings_failure(mock_put, headers):
    mock_put.return_value = MagicMock(status_code=500, text="Error")
    with pytest.raises(Exception, match="Setting project prearchive settings failed"):
        set_project_prearchive_settings("TEST", headers)


# ===========================================================================
# get_command_info
# ===========================================================================
_PIN = "ghcr.io/londonaicentre/xnat-dcm2niix:v1.0.20260724"
_OLD = "xnat/dcm2niix:latest"


def _available(command_id: int = 5, wrapper_id: int = 5, image: str = _PIN, extra: dict | None = None) -> dict:
    """One row of GET /xapi/commands/available/site, as dev trust 1 answered it."""
    row = {
        "command-id": command_id,
        "command-name": "dcm2niix",
        "wrapper-id": wrapper_id,
        "wrapper-name": "dcm2niix-scan",
        "wrapper-contexts": ["xnat:imageScanData"],
        "image-name": image,
        "image-type": "docker",
        "enabled": True,
        "root-element-name": "scan",
    }
    row.update(extra or {})
    return row


def _site_commands(*rows) -> MagicMock:
    return MagicMock(status_code=200, json=MagicMock(return_value=list(rows)))


@patch("imaging_api.services.projects.requests.get")
def test_get_command_info_takes_ids_from_the_sites_dcm2niix_row(mock_get, headers):
    mock_get.return_value = _site_commands(
        _available(command_id=2, wrapper_id=9, extra={"wrapper-name": "other-scan"}),
        _available(command_id=4, wrapper_id=4, image=_OLD),
        _available(command_id=1, wrapper_id=3),
    )

    assert get_command_info("TEST", _PIN, headers) == (1, "dcm2niix-scan", 3)
    url = urlsplit(mock_get.call_args[0][0])
    # The site listing: the project's own can come back empty for a project created moments ago.
    assert url.path.endswith("/xapi/commands/available/site")
    assert parse_qs(url.query) == {"xsiType": ["xnat:imageScanData"]}


@patch("imaging_api.services.projects.requests.get")
def test_get_command_info_refuses_a_wrapper_the_site_has_not_enabled(mock_get, headers):
    """XNAT would refuse the trigger as invalid, so say why before creating it."""
    mock_get.return_value = _site_commands(_available(extra={"enabled": False}))

    with pytest.raises(Exception, match="not enabled site-wide") as excinfo:
        get_command_info("TEST", _PIN, headers)

    assert "configure-dcm2niix.sh" in str(excinfo.value)


@patch("imaging_api.services.projects.requests.get")
@pytest.mark.parametrize(
    ("rows", "named"),
    [
        ([], "none"),
        ([_available(extra={"wrapper-name": "other-scan"})], "none"),
        ([_available(command_id=4, image=_OLD)], _OLD),  # an XNAT registered before the current pin
        ([_available(command_id=4), _available(command_id=6)], _PIN),
    ],
    ids=["empty", "other-wrapper-only", "older-image", "two-pinned"],
)
def test_get_command_info_refuses_without_exactly_one_pinned_wrapper(mock_get, headers, rows, named):
    mock_get.return_value = _site_commands(*rows)

    with pytest.raises(Exception, match=f"wrappers on the pinned '{_PIN}'") as excinfo:
        get_command_info("TEST", _PIN, headers)

    message = str(excinfo.value)
    assert f"registered images: {named})" in message
    assert "configure-dcm2niix.sh" in message


@patch("imaging_api.services.projects.requests.get")
def test_get_command_info_messages_stay_inside_the_truncation_budget(mock_get, headers):
    """trust-api relays at most 1000 characters to the hub; the remedy must survive the cut."""
    images = [f"registry.example.org/{'a' * 40}/xnat-dcm2niix:v{i}" for i in range(10)]
    mock_get.return_value = _site_commands(*(_available(command_id=i, image=image) for i, image in enumerate(images)))

    with pytest.raises(Exception, match="wrappers on the pinned") as excinfo:
        get_command_info(str(uuid4()), _PIN, headers)

    message = str(excinfo.value)
    assert len(message) < 1000, len(message)
    assert message.endswith("(TROUBLESHOOTING 2.3c).")


@patch("imaging_api.services.projects.requests.get")
def test_get_command_info_listing_failure_is_bounded(mock_get, headers):
    page = "<html><head><title>HTTP Status 403</title><style>" + "x" * 600 + "</style></head></html>"
    mock_get.return_value = MagicMock(status_code=403, text=page)

    with pytest.raises(Exception, match="failed: 403") as excinfo:
        get_command_info("TEST", _PIN, headers)

    assert "full body in the imaging-api log" in str(excinfo.value)
    assert len(str(excinfo.value)) < 1000


# ===========================================================================
# get_wrapper_action_key
# ===========================================================================
_PROVIDER = "org.nrg.containers.services.CommandActionProvider:"


@patch("imaging_api.services.projects.requests.get")
def test_get_wrapper_action_key_takes_the_key_xnat_lists_for_the_wrapper(mock_get, headers):
    actions = [
        {"action-key": f"{_PROVIDER}3", "display-name": "other-wrapper"},
        {"action-key": f"{_PROVIDER}4", "display-name": "dcm2niix-scan"},
        {"action-key": "org.nrg.xnat.eventservice.actions.EventServiceLoggingAction:logger", "display-name": "Logger"},
        {"action-key": "org.example.OtherActionProvider:5", "display-name": "another provider's action 5"},
        {"action-key": f"{_PROVIDER}5", "display-name": "dcm2niix-scan", "description": "Run dcm2niix on a Scan"},
    ]
    mock_get.return_value = MagicMock(status_code=200, json=MagicMock(return_value=actions))

    key = get_wrapper_action_key(5, "dcm2niix-scan", headers)

    assert key == f"{_PROVIDER}5"
    assert mock_get.call_args[0][0].endswith("/xapi/events/allactions")


@patch("imaging_api.services.projects.requests.get")
@pytest.mark.parametrize(
    "actions",
    [
        [],
        None,
        [{"action-key": f"{_PROVIDER}4", "display-name": "dcm2niix-scan"}],
        [{"action-key": f"{_PROVIDER}5", "display-name": "dcm2niix-scan"}] * 2,
        [
            {"action-key": f"{_PROVIDER}5", "display-name": "dcm2niix-scan"},
            {"action-key": "some.other.Provider:5", "display-name": "dcm2niix-scan"},
        ],
    ],
    ids=["empty", "null", "other-wrapper-only", "duplicate", "two-providers"],
)
def test_get_wrapper_action_key_refuses_anything_but_one_match(mock_get, headers, actions):
    """A guessed key is a trigger XNAT reports invalid, which is the FLIP#1451 failure."""
    mock_get.return_value = MagicMock(status_code=200, json=MagicMock(return_value=actions))

    with pytest.raises(Exception, match="expected exactly one"):
        get_wrapper_action_key(5, "dcm2niix-scan", headers)


@patch("imaging_api.services.projects.requests.get")
def test_get_wrapper_action_key_listing_failure(mock_get, headers):
    mock_get.return_value = MagicMock(status_code=403, text="Forbidden")

    with pytest.raises(Exception, match="action listing failed: 403"):
        get_wrapper_action_key(5, "dcm2niix-scan", headers)


# ===========================================================================
# create_project_event_subscription
# ===========================================================================
@patch("imaging_api.services.projects.get_wrapper_action_key", return_value=f"{_PROVIDER}3")
@patch("imaging_api.services.projects.requests.post")
@patch("imaging_api.services.projects.requests.put")
@patch("imaging_api.services.projects.get_command_info")
def test_create_project_event_subscription_active(mock_cmd_info, mock_put, mock_post, mock_key, headers):
    mock_cmd_info.return_value = (1, "dcm2niix-scan", 3)
    mock_put.return_value = MagicMock(status_code=200)
    mock_post.return_value = MagicMock(status_code=200)

    create_project_event_subscription("TEST", "ghcr.io/londonaicentre/xnat-dcm2niix:v1.0.20260724", True, headers)

    mock_cmd_info.assert_called_once_with("TEST", "ghcr.io/londonaicentre/xnat-dcm2niix:v1.0.20260724", headers)
    mock_put.assert_called_once()
    assert "/commands/1/wrappers/dcm2niix-scan/enabled" in mock_put.call_args[0][0]
    mock_post.assert_called_once()
    call_url = mock_post.call_args[0][0]
    call_payload = mock_post.call_args[1]["json"]
    assert "/xapi/projects/TEST/events/subscription" in call_url
    assert call_payload["active"] is True
    assert call_payload["name"] == "DICOM-NIfTI Conversion"
    # The key XNAT lists for the wrapper (id 3), not one formatted from the command id (1).
    mock_key.assert_called_once_with(3, "dcm2niix-scan", headers)
    assert call_payload["action-key"] == f"{_PROVIDER}3"


@patch("imaging_api.services.projects.get_wrapper_action_key", return_value=f"{_PROVIDER}3")
@patch("imaging_api.services.projects.requests.post")
@patch("imaging_api.services.projects.requests.put")
@patch("imaging_api.services.projects.get_command_info")
def test_create_project_event_subscription_inactive(mock_cmd_info, mock_put, mock_post, mock_key, headers):
    mock_cmd_info.return_value = (1, "dcm2niix-scan", 3)
    mock_put.return_value = MagicMock(status_code=200)
    mock_post.return_value = MagicMock(status_code=200)

    create_project_event_subscription("TEST", "ghcr.io/londonaicentre/xnat-dcm2niix:v1.0.20260724", False, headers)

    mock_post.assert_called_once()
    call_payload = mock_post.call_args[1]["json"]
    assert call_payload["active"] is False


@patch("imaging_api.services.projects.requests.put")
@patch("imaging_api.services.projects.get_command_info")
def test_create_project_event_subscription_enable_command_failure(mock_cmd_info, mock_put, headers):
    mock_cmd_info.return_value = (1, "dcm2niix-scan", 3)
    mock_put.return_value = MagicMock(status_code=500, text="Internal Server Error")

    with pytest.raises(Exception, match="Enabling command"):
        create_project_event_subscription("TEST", "ghcr.io/londonaicentre/xnat-dcm2niix:v1.0.20260724", True, headers)


@patch("imaging_api.services.projects.get_wrapper_action_key", return_value=f"{_PROVIDER}3")
@patch("imaging_api.services.projects.requests.post")
@patch("imaging_api.services.projects.requests.put")
@patch("imaging_api.services.projects.get_command_info")
def test_create_project_event_subscription_failure(mock_cmd_info, mock_put, mock_post, mock_key, headers):
    mock_cmd_info.return_value = (1, "dcm2niix-scan", 3)
    mock_put.return_value = MagicMock(status_code=200)
    mock_post.return_value = MagicMock(status_code=500, text="Internal Server Error")

    with pytest.raises(Exception, match="Creating event subscription"):
        create_project_event_subscription("TEST", "ghcr.io/londonaicentre/xnat-dcm2niix:v1.0.20260724", True, headers)


# ===========================================================================
# add_central_hub_users_to_project
# ===========================================================================
@patch("imaging_api.services.projects.add_user_to_project")
@patch("imaging_api.services.projects.get_user_profile_by")
def test_add_central_hub_users_no_users(mock_get_profile, mock_add, headers):
    hub_project = CentralHubProject(
        project_id=uuid4(),
        trust_id=uuid4(),
        project_name="Proj",
        query="SELECT *",
        users=[],
    )
    created, added = add_central_hub_users_to_project(hub_project, "TEST", headers)
    assert created == []
    assert added == []


@patch("imaging_api.services.projects.issue_invite")
@patch("imaging_api.services.projects.add_user_to_project")
@patch("imaging_api.services.projects.get_user_profile_by")
def test_add_central_hub_users_existing_user(mock_get_profile, mock_add, mock_invite, headers):
    """An existing account that has logged in before gets the added-to-project notice, not an invite."""
    user_profile = User(**_USER_DICT, lastSuccessfulLogin=1780012450777)
    mock_get_profile.return_value = user_profile
    mock_add.return_value = user_profile

    hub_user = CentralHubUser(id=uuid4(), email="alice@test.com")
    hub_project = CentralHubProject(
        project_id=uuid4(),
        trust_id=uuid4(),
        project_name="Proj",
        query="SELECT *",
        users=[hub_user],
    )

    created, added = add_central_hub_users_to_project(hub_project, "TEST", headers)
    assert created == []
    assert added == [user_profile]
    mock_invite.assert_not_called()


@patch("imaging_api.services.users.encrypt", return_value="enc")
@patch("imaging_api.services.users.issue_setup_token", return_value="/app/template/XDATScreen_UpdateUser.vm?a=al&s=se")
@patch("imaging_api.services.projects.add_user_to_project")
@patch("imaging_api.services.projects.get_user_profile_by")
def test_add_central_hub_users_existing_user_never_logged_in_is_reinvited(
    mock_get_profile, mock_add, mock_issue, mock_encrypt, headers
):
    """An account that exists but has never authenticated is re-invited: a lost/expired first invite, or a
    token issuance that failed after the account was created, must not dead-end on "log in with your
    existing credentials"."""
    user_profile = User(**_USER_DICT)  # lastSuccessfulLogin omitted -> None, as XNAT reports it
    mock_get_profile.return_value = user_profile
    mock_add.return_value = user_profile

    hub_user = CentralHubUser(id=uuid4(), email="alice@test.com")
    hub_project = CentralHubProject(
        project_id=uuid4(),
        trust_id=uuid4(),
        project_name="Proj",
        query="SELECT *",
        users=[hub_user],
    )

    created, added = add_central_hub_users_to_project(hub_project, "TEST", headers)

    assert created == [CreatedUser(username="alice", encrypted_setup_path="enc", email="alice@test.com")]
    assert added == []  # the invite names the project; no contradictory second email
    mock_issue.assert_called_once_with("alice", headers)
    mock_add.assert_called_once_with(user_profile, "TEST", headers)  # still added to the project


@patch("imaging_api.services.users.issue_setup_token", side_effect=Exception("Error: XNAT setup-token issuance failed"))
@patch("imaging_api.services.projects.add_user_to_project")
@patch("imaging_api.services.projects.get_user_profile_by")
def test_add_central_hub_users_reinvite_token_failure_raises(mock_get_profile, mock_add, mock_issue, headers):
    """A failed re-issue surfaces like a failed first issue (the task fails and is retried), not silently."""
    mock_get_profile.return_value = User(**_USER_DICT)

    hub_project = CentralHubProject(
        project_id=uuid4(),
        trust_id=uuid4(),
        project_name="Proj",
        query="SELECT *",
        users=[CentralHubUser(id=uuid4(), email="alice@test.com")],
    )

    with pytest.raises(Exception, match="setup-token issuance failed"):
        add_central_hub_users_to_project(hub_project, "TEST", headers)
    mock_add.assert_not_called()


@patch("imaging_api.services.projects.add_user_to_project")
@patch("imaging_api.services.projects.create_user_from_central_hub_user")
@patch("imaging_api.services.projects.get_user_profile_by")
def test_add_central_hub_users_new_user(mock_get_profile, mock_create, mock_add, headers):
    mock_get_profile.side_effect = NotFoundError("not found")
    user_profile = User(**_USER_DICT)
    created_user = CreatedUser(username="alice", encrypted_setup_path="enc", email="alice@test.com")
    mock_create.return_value = (created_user, user_profile)
    mock_add.return_value = user_profile

    hub_user = CentralHubUser(id=uuid4(), email="alice@test.com")
    hub_project = CentralHubProject(
        project_id=uuid4(),
        trust_id=uuid4(),
        project_name="Proj",
        query="SELECT *",
        users=[hub_user],
    )

    created, added = add_central_hub_users_to_project(hub_project, "TEST", headers)
    assert created == [created_user]
    assert added == []  # invited this run -> no added-to-project notice on top of the invite
    mock_add.assert_called_once_with(user_profile, "TEST", headers)


@patch("imaging_api.services.projects.add_user_to_project")
@patch("imaging_api.services.projects.get_user_profile_by")
def test_add_central_hub_users_disabled_user_skipped(mock_get_profile, mock_add, headers):
    hub_user = CentralHubUser(id=uuid4(), email="disabled@test.com", is_disabled=True)
    hub_project = CentralHubProject(
        project_id=uuid4(),
        trust_id=uuid4(),
        project_name="Proj",
        query="SELECT *",
        users=[hub_user],
    )

    created, added = add_central_hub_users_to_project(hub_project, "TEST", headers)
    assert created == []
    assert added == []
    mock_get_profile.assert_not_called()


# ===========================================================================
# delete_queued_import_requests
# ===========================================================================
@pytest.mark.asyncio
@patch("imaging_api.services.projects.get_queued_pacs_request_by_project")
@patch("imaging_api.services.projects.requests.post")
async def test_delete_queued_import_requests_success(mock_post, mock_get_queued, headers):
    mock_get_queued.return_value = [
        QueuedPacsRequest(
            id=1,
            created="2023-10-01T00:00:00",
            accession_number="FAK57777617",
            status="QUEUED",
            xnat_project="TEST",
        )
    ]
    mock_post.return_value = MagicMock(status_code=200)

    async def fake_session():
        yield MagicMock()

    with patch("imaging_api.services.projects.get_session", side_effect=lambda: fake_session()):
        result = await delete_queued_import_requests("TEST", headers)
    assert result is True


@pytest.mark.asyncio
@patch("imaging_api.services.projects.get_queued_pacs_request_by_project")
async def test_delete_queued_import_requests_none_to_delete(mock_get_queued, headers):
    mock_get_queued.return_value = []

    async def fake_session():
        yield MagicMock()

    with patch("imaging_api.services.projects.get_session", side_effect=lambda: fake_session()):
        result = await delete_queued_import_requests("TEST", headers)
    assert result is False


@pytest.mark.asyncio
@patch("imaging_api.services.projects.get_queued_pacs_request_by_project")
@patch("imaging_api.services.projects.requests.post")
async def test_delete_queued_import_requests_post_failure(mock_post, mock_get_queued, headers):
    mock_get_queued.return_value = [
        QueuedPacsRequest(
            id=1,
            created="2023-10-01T00:00:00",
            accession_number="FAK57777617",
            status="QUEUED",
            xnat_project="TEST",
        )
    ]
    mock_post.return_value = MagicMock(status_code=500, text="Error")

    async def fake_session():
        yield MagicMock()


# ===========================================================================
# delete_project
# ===========================================================================
@pytest.mark.asyncio
@patch("imaging_api.services.projects.delete_queued_import_requests", new_callable=AsyncMock)
@patch("imaging_api.services.projects.get_project")
@patch("imaging_api.services.projects.requests.delete")
async def test_delete_project_success(mock_delete, mock_get_project, mock_del_queued, headers):
    mock_delete.return_value = MagicMock(status_code=200)
    mock_get_project.return_value = Project(**_PROJECT_DICT)
    mock_del_queued.return_value = True

    project = await delete_project("TEST", headers)
    assert project.ID == "TEST"


@pytest.mark.asyncio
@patch("imaging_api.services.projects.get_project")
@patch("imaging_api.services.projects.requests.delete")
async def test_delete_project_failure(mock_delete, mock_get_project, headers):
    mock_delete.return_value = MagicMock(status_code=500, text="Error")
    mock_get_project.return_value = Project(**_PROJECT_DICT)

    with pytest.raises(Exception, match="XNAT project deletion failed"):
        await delete_project("TEST", headers)


# ===========================================================================
# get_subjects
# ===========================================================================
@patch("imaging_api.services.projects.requests.get")
@patch("imaging_api.services.projects.get_project")
def test_get_subjects_success(mock_get_project, mock_get, headers):
    mock_get_project.return_value = Project(**_PROJECT_DICT)
    mock_get.return_value = MagicMock(
        status_code=200,
        json=MagicMock(
            return_value={
                "ResultSet": {
                    "Result": [
                        {
                            "ID": "S1",
                            "label": "subj1",
                            "insert_date": "2023-01-01",
                            "project": "TEST",
                            "insert_user": "admin",
                            "URI": "/subjects/S1",
                        },
                    ]
                }
            }
        ),
    )
    subjects = get_subjects("TEST", headers)
    assert len(subjects) == 1
    assert subjects[0].label == "subj1"


@patch("imaging_api.services.projects.requests.get")
@patch("imaging_api.services.projects.get_project")
def test_get_subjects_failure(mock_get_project, mock_get, headers):
    mock_get_project.return_value = Project(**_PROJECT_DICT)
    mock_get.return_value = MagicMock(status_code=500, text="Error")
    with pytest.raises(Exception, match="XNAT subjects fetch failed"):
        get_subjects("TEST", headers)


# ===========================================================================
# get_experiments
# ===========================================================================
@patch("imaging_api.services.projects.requests.get")
@patch("imaging_api.services.projects.get_project")
def test_get_experiments_success(mock_get_project, mock_get, headers):
    mock_get_project.return_value = Project(**_PROJECT_DICT)
    mock_get.return_value = MagicMock(
        status_code=200,
        json=MagicMock(
            return_value={
                "ResultSet": {
                    "Result": [
                        {
                            "ID": "E1",
                            "label": "exp1",
                            "date": "2023-01-01",
                            "project": "TEST",
                            "insert_date": "2023-01-01",
                            "xsiType": "xnat:ctScanData",
                            "URI": "/exp/E1",
                        },
                    ]
                }
            }
        ),
    )
    experiments = get_experiments("TEST", headers)
    assert len(experiments) == 1
    assert experiments[0].label == "exp1"


@patch("imaging_api.services.projects.requests.get")
@patch("imaging_api.services.projects.get_project")
def test_get_experiments_failure(mock_get_project, mock_get, headers):
    mock_get_project.return_value = Project(**_PROJECT_DICT)
    # A real XNAT non-200 serves an HTML/plain-text body, so .json() raises. The status must be
    # checked before the body is parsed, otherwise the JSON error masks the true HTTP status.
    mock_get.return_value = MagicMock(
        status_code=500,
        text="Error",
        json=MagicMock(side_effect=ValueError("no json")),
    )
    with pytest.raises(XnatFetchError, match="XNAT experiments fetch failed"):
        get_experiments("TEST", headers)


@patch("imaging_api.services.projects.requests.get")
@patch("imaging_api.services.projects.get_project")
def test_get_experiments_uses_unfiltered_global_listing(mock_get_project, mock_get, headers):
    # Regression guard: get_experiments must query the GLOBAL experiments endpoint filtered by
    # project, NOT the project-scoped /data/projects/{id}/experiments. The project-scoped listing
    # is filtered by per-data-type element security, so sessions whose modality is not registered
    # there (e.g. xnat:dxSessionData for chest X-rays) are silently omitted and the import shows
    # "0 imported". The global listing returns identical fields without that filter.
    mock_get_project.return_value = Project(**_PROJECT_DICT)
    mock_get.return_value = MagicMock(
        status_code=200,
        json=MagicMock(return_value={"ResultSet": {"Result": []}}),
    )

    get_experiments("TEST", headers)

    called = mock_get.call_args
    # Global endpoint, project passed as a (URL-encoded) query parameter — NOT the project-scoped path.
    assert called.args[0].endswith("/data/experiments")
    assert called.kwargs["params"] == {"project": "TEST"}
    assert "/data/projects/TEST/experiments" not in called.args[0]


# ===========================================================================
# get_experiment
# ===========================================================================
@patch("imaging_api.services.projects.requests.get")
@patch("imaging_api.services.projects.get_project")
def test_get_experiment_success(mock_get_project, mock_get, headers):
    mock_get_project.return_value = Project(**_PROJECT_DICT)
    expected = {"items": [{"data_fields": {"subject_ID": "S1"}}]}
    mock_get.return_value = MagicMock(status_code=200, json=MagicMock(return_value=expected))

    result = get_experiment("TEST", "exp1", headers)
    assert result == expected


@patch("imaging_api.services.projects.requests.get")
@patch("imaging_api.services.projects.get_project")
def test_get_experiment_not_found(mock_get_project, mock_get, headers):
    mock_get_project.return_value = Project(**_PROJECT_DICT)
    mock_get.return_value = MagicMock(status_code=404)

    with pytest.raises(NotFoundError, match="not found"):
        get_experiment("TEST", "missing", headers)


@patch("imaging_api.services.projects.requests.get")
@patch("imaging_api.services.projects.get_project")
def test_get_experiment_server_error(mock_get_project, mock_get, headers):
    mock_get_project.return_value = Project(**_PROJECT_DICT)
    mock_get.return_value = MagicMock(status_code=500, text="Error")

    with pytest.raises(Exception, match="XNAT experiment fetch failed"):
        get_experiment("TEST", "exp1", headers)


# ===========================================================================
# get_subject_id_from_experiment_response
# ===========================================================================
def test_get_subject_id_from_experiment_response_success():
    response = {"items": [{"data_fields": {"subject_ID": "SUBJ_123"}}]}
    assert get_subject_id_from_experiment_response(response) == "SUBJ_123"


def test_get_subject_id_from_experiment_response_bad_data():
    with pytest.raises(Exception, match="Failed to parse XNAT experiment data"):
        get_subject_id_from_experiment_response({})


def test_identifiers_stay_in_one_segment_at_http_boundary(xnat_path_segment, sent_xnat_requests):
    raw, encoded = xnat_path_segment
    with patch("imaging_api.services.projects.get_project"):
        assert get_experiment(raw, raw, {"X-Request-ID": "trace-1"}) == {}

    assert len(sent_xnat_requests) == 1
    request = sent_xnat_requests[0]
    parsed = urlsplit(request.url)
    assert parsed.path == f"/data/projects/{encoded}/experiments/{encoded}"
    assert parsed.query == "format=json"
    assert parsed.fragment == ""
    assert request.headers["X-Request-ID"] == "trace-1"


@pytest.mark.parametrize("invalid", ["", ".", ".."])
@pytest.mark.parametrize("field", ["project_id", "experiment_id_or_label"])
def test_empty_and_dot_segments_fail_before_http(field, invalid, sent_xnat_requests):
    kwargs = {"project_id": "PROJ", "experiment_id_or_label": "EXP", "headers": {}, field: invalid}
    with pytest.raises(ValueError, match="empty or a dot-segment"):
        get_experiment(**kwargs)
    assert sent_xnat_requests == []


def test_experiment_lookup_quotes_identifiers_after_real_project_listing(sent_xnat_requests):
    project_id = "PROJ?format=xml"
    project = {
        "ID": project_id,
        "secondary_ID": "hub-project",
        "name": "Project",
        "pi_firstname": "",
        "pi_lastname": "",
        "URI": "/data/projects/project",
    }
    with patch.object(requests.Response, "json", side_effect=[{"ResultSet": {"Result": [project]}}, {"items": []}]):
        assert get_experiment(project_id, "ACC#fragment", {}) == {"items": []}
    assert [urlsplit(request.url).path for request in sent_xnat_requests] == [
        "/data/projects",
        "/data/projects/PROJ%3Fformat%3Dxml/experiments/ACC%23fragment",
    ]
    assert urlsplit(sent_xnat_requests[1].url).query == "format=json"


# ===========================================================================
# Every request URL goes through xnat_url (#1386): ordinary identifiers produce the same URL the
# f-strings did. ``sent_xnat_requests`` records the prepared requests, so the comparison is against
# what Requests would have sent for the old literal, not against a raw string.
# ===========================================================================
_XNAT = get_settings().XNAT_URL
_PROJECT_ID = "6f1c2a9e-3b7d-4c55-9a0e-2d4f8b1c7e10"


def _prepared(method: str, url: str) -> str:
    prepared = requests.Request(method, url).prepare().url
    assert prepared is not None
    return prepared


@pytest.mark.asyncio
async def test_delete_project_url_is_unchanged_for_an_ordinary_project_id(sent_xnat_requests):
    with (
        patch("imaging_api.services.projects.get_project", return_value=Project(**_PROJECT_DICT)),
        patch("imaging_api.services.projects.delete_queued_import_requests", new_callable=AsyncMock),
    ):
        await delete_project(_PROJECT_ID, {})

    assert [(r.method, r.url) for r in sent_xnat_requests] == [
        ("DELETE", _prepared("DELETE", f"{_XNAT}/data/projects/{_PROJECT_ID}?removeFiles=true")),
    ]


@pytest.mark.asyncio
async def test_delete_project_keeps_a_hostile_id_out_of_the_query(sent_xnat_requests):
    """A project id cannot add or override query parameters on the destructive DELETE."""
    with (
        patch("imaging_api.services.projects.get_project", return_value=Project(**_PROJECT_DICT)),
        patch("imaging_api.services.projects.delete_queued_import_requests", new_callable=AsyncMock),
    ):
        await delete_project("PROJ?removeFiles=false&x=", {})

    parsed = urlsplit(sent_xnat_requests[0].url)
    assert parsed.path == "/data/projects/PROJ%3FremoveFiles%3Dfalse%26x%3D"
    assert parsed.query == "removeFiles=true"


def test_command_lookup_is_the_site_listing_with_one_query_value(sent_xnat_requests):
    listing = [{"command-id": 7, "wrapper-id": 7, "wrapper-name": "dcm2niix-scan", "image-name": "img:1.0"}]
    with patch.object(requests.Response, "json", return_value=listing):
        assert get_command_info(_PROJECT_ID, "img:1.0", {}) == (7, "dcm2niix-scan", 7)

    parsed = urlsplit(sent_xnat_requests[0].url)
    assert parsed.path == "/xapi/commands/available/site"
    assert parse_qs(parsed.query) == {"xsiType": ["xnat:imageScanData"]}


def test_command_lookup_never_puts_the_project_id_in_the_url(sent_xnat_requests, xnat_path_segment):
    raw, _ = xnat_path_segment
    listing = [{"command-id": 7, "wrapper-id": 7, "wrapper-name": "dcm2niix-scan", "image-name": "img:1.0"}]
    with patch.object(requests.Response, "json", return_value=listing):
        get_command_info(raw, "img:1.0", {})

    parsed = urlsplit(sent_xnat_requests[0].url)
    assert parsed.path == "/xapi/commands/available/site"
    assert parse_qs(parsed.query) == {"xsiType": ["xnat:imageScanData"]}
    assert parsed.fragment == ""


def test_project_scoped_urls_are_unchanged_for_an_ordinary_project_id(sent_xnat_requests):
    with (
        patch("imaging_api.services.projects.get_project"),
        patch("imaging_api.services.projects.get_command_info", return_value=(7, "dcm2niix-scan", 7)),
        patch("imaging_api.services.projects.get_wrapper_action_key", return_value="provider:7"),
        patch.object(requests.Response, "json", return_value={"ResultSet": {"Result": []}}),
    ):
        set_project_prearchive_settings(_PROJECT_ID, {})
        create_project_event_subscription(_PROJECT_ID, "img:1.0", True, {})
        get_subjects(_PROJECT_ID, {})
        get_experiments(_PROJECT_ID, {})
        get_all_projects({})

    expected = [
        ("PUT", f"{_XNAT}/data/projects/{_PROJECT_ID}/prearchive_code/4"),
        ("PUT", f"{_XNAT}/xapi/projects/{_PROJECT_ID}/commands/7/wrappers/dcm2niix-scan/enabled"),
        ("POST", f"{_XNAT}/xapi/projects/{_PROJECT_ID}/events/subscription"),
        ("GET", f"{_XNAT}/data/projects/{_PROJECT_ID}/subjects"),
        ("GET", f"{_XNAT}/data/experiments?project={_PROJECT_ID}"),
        ("GET", f"{_XNAT}/data/projects"),
    ]
    assert [(r.method, r.url) for r in sent_xnat_requests] == [(m, _prepared(m, u)) for m, u in expected]


def test_project_scoped_urls_keep_a_hostile_project_id_in_one_segment(sent_xnat_requests, xnat_path_segment):
    raw, encoded = xnat_path_segment
    with (
        patch("imaging_api.services.projects.get_project"),
        patch.object(requests.Response, "json", return_value={"ResultSet": {"Result": []}}),
    ):
        set_project_prearchive_settings(raw, {})
        get_subjects(raw, {})

    assert [urlsplit(r.url).path for r in sent_xnat_requests] == [
        f"/data/projects/{encoded}/prearchive_code/4",
        f"/data/projects/{encoded}/subjects",
    ]
    assert all(urlsplit(r.url).query == "" for r in sent_xnat_requests)
