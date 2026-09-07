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
from uuid import uuid4

import pytest

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
        'evil</name><name>injected',
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
@patch("imaging_api.services.projects.requests.get")
def test_get_command_info_success(mock_get, headers):
    mock_get.return_value = MagicMock(
        status_code=200,
        json=MagicMock(return_value=[{"id": 1, "xnat": [{"name": "dcm2niix-scan"}]}]),
    )

    command_id, wrapper_name = get_command_info("ghcr.io/londonaicentre/xnat-dcm2niix:v1.0.20260724", headers)

    assert command_id == 1
    assert wrapper_name == "dcm2niix-scan"


@patch("imaging_api.services.projects.requests.get")
def test_get_command_info_fetch_failure(mock_get, headers):
    mock_get.return_value = MagicMock(status_code=500, text="Internal Server Error")

    with pytest.raises(Exception, match="XNAT command fetch failed"):
        get_command_info("ghcr.io/londonaicentre/xnat-dcm2niix:v1.0.20260724", headers)


@patch("imaging_api.services.projects.requests.get")
def test_get_command_info_lists_registered_commands_on_mismatch(mock_get, headers):
    """The real FLIP#980 case: same tool, different repo AND tag (FLIP#1093)."""
    filtered = MagicMock(status_code=200, json=MagicMock(return_value=[]))
    unfiltered = MagicMock(
        status_code=200,
        json=MagicMock(return_value=[{"id": 6, "name": "dcm2niix", "image": "xnat/dcm2niix:latest"}]),
    )
    mock_get.side_effect = [filtered, unfiltered]

    with pytest.raises(Exception, match="No commands found for container") as excinfo:
        get_command_info("ghcr.io/londonaicentre/xnat-dcm2niix:v1.0.20260724", headers)

    message = str(excinfo.value)
    assert "ghcr.io/londonaicentre/xnat-dcm2niix:v1.0.20260724" in message
    assert "'dcm2niix' -> 'xnat/dcm2niix:latest'" in message
    assert "configure-dcm2niix.sh" in message
    # The misleading hypothesis must not appear when commands plainly exist.
    assert "may not be installed" not in message
    # The second call must be the UNFILTERED listing. Driving these tests purely by side_effect
    # ordering would stay green if a regression re-queried the filtered URL, which always comes
    # back empty and would silently degrade every mismatch into the plugin hypothesis.
    first_url, second_url = (call.args[0] for call in mock_get.call_args_list)
    assert "image=" in first_url
    assert second_url.endswith("/xapi/commands")
    assert "image=" not in second_url


@patch("imaging_api.services.projects.requests.get")
def test_get_command_info_lists_unrelated_commands_without_claiming_a_mismatch(mock_get, headers):
    """Unrelated containers must be shown as-is, not implied to be stale versions."""
    filtered = MagicMock(status_code=200, json=MagicMock(return_value=[]))
    unfiltered = MagicMock(
        status_code=200,
        json=MagicMock(
            return_value=[
                {"id": 2, "name": "some-other-tool", "image": "ghcr.io/someone/other-tool:v3"},
                {"id": 3, "name": "another", "image": "docker.io/library/busybox:1.36"},
            ]
        ),
    )
    mock_get.side_effect = [filtered, unfiltered]

    with pytest.raises(Exception, match="No commands found for container") as excinfo:
        get_command_info("ghcr.io/londonaicentre/xnat-dcm2niix:v1.0.20260724", headers)

    message = str(excinfo.value)
    assert "2 command(s) registered" in message
    assert "'some-other-tool' -> 'ghcr.io/someone/other-tool:v3'" in message
    assert "'another' -> 'docker.io/library/busybox:1.36'" in message
    # It is conditional ("If one of those..."), never an assertion about these images.
    assert "If one of those" in message


@patch("imaging_api.services.projects.requests.get")
def test_get_command_info_empty_registry_leads_with_the_registration_fix(mock_get, headers):
    """A readable, empty registry means "never registered", not "plugin missing".

    XNAT answers a path with no plugin behind it with 404, so a 200 carrying an empty array is
    evidence the Container Service *is* installed. TROUBLESHOOTING 2.3a documents the cause this
    branch actually has: the service account lacked ContainerManager, so registration reported
    success having registered nothing. Lead with that remedy, not with the plugin hypothesis.
    """
    empty = MagicMock(status_code=200, json=MagicMock(return_value=[]))
    mock_get.side_effect = [empty, empty]

    with pytest.raises(Exception, match="No commands found for container") as excinfo:
        get_command_info("ghcr.io/londonaicentre/xnat-dcm2niix:v1.0.20260724", headers)

    message = str(excinfo.value)
    # The fact the response supports, and the action that follows from it.
    assert "readable and empty" in message
    assert "configure-dcm2niix.sh" in message
    assert "ContainerManager" in message
    assert "2.3a" in message
    # The plugin hypothesis survives only as a trailing note, never as the headline.
    assert not message.startswith("No commands found for container '...' - Container Service")
    assert message.index("configure-dcm2niix.sh") < message.index("missing plugin")


@patch("imaging_api.services.projects.requests.get")
def test_get_command_info_diagnostic_failure_does_not_mask_error(mock_get, headers):
    """A failed re-query must raise the real mismatch error AND not invent a cause.

    Asserting only the generic prefix left this test passing even when the code claimed an empty
    registry, so it could not hold the branch it exists for.
    """
    filtered = MagicMock(status_code=200, json=MagicMock(return_value=[]))
    mock_get.side_effect = [filtered, ConnectionError("xnat unreachable")]

    with pytest.raises(Exception, match="No commands found for container") as excinfo:
        get_command_info("ghcr.io/londonaicentre/xnat-dcm2niix:v1.0.20260724", headers)

    message = str(excinfo.value)
    assert "could not be listed" in message
    assert "ConnectionError" in message
    # A failure to list is not evidence about the plugin.
    assert "may not be installed" not in message
    assert "registered at all" not in message


@patch("imaging_api.services.projects.requests.get")
def test_get_command_info_unauthorised_listing_is_not_a_missing_plugin(mock_get, headers):
    """The live failure this distinction exists for.

    XNAT answers /xapi/commands with 401 and an HTML body when the service account's credentials
    are wrong or its session expired. Reporting that as a missing Container Service sends the
    operator to reinstall a plugin that is installed and healthy.
    """
    filtered = MagicMock(status_code=200, json=MagicMock(return_value=[]))
    unauthorised = MagicMock(status_code=401, text="<html>login</html>")
    mock_get.side_effect = [filtered, unauthorised]

    with pytest.raises(Exception, match="No commands found for container") as excinfo:
        get_command_info("ghcr.io/londonaicentre/xnat-dcm2niix:v1.0.20260724", headers)

    message = str(excinfo.value)
    assert "HTTP 401" in message
    assert "may not be installed" not in message


@patch("imaging_api.services.projects.requests.get")
@pytest.mark.parametrize(
    "body",
    [
        {"detail": "not an array"},
        ["a bare string", 42],
    ],
    ids=["object-body", "list-of-non-objects"],
)
def test_get_command_info_malformed_listing_body_still_reports_the_mismatch(mock_get, headers, body):
    """A 200 that is not an array of objects must not raise out of the diagnostic helper.

    `sorted()` over a dict yields its string keys, so formatting would hit
    `AttributeError: 'str' object has no attribute 'get'` and replace the mismatch message with a
    crash on its way to the hub. Live XNAT does not emit this shape (faults come back as non-200),
    so it guards against an intermediary that rewrites the body.
    """
    filtered = MagicMock(status_code=200, json=MagicMock(return_value=[]))
    malformed = MagicMock(status_code=200, json=MagicMock(return_value=body))
    mock_get.side_effect = [filtered, malformed]

    with pytest.raises(Exception, match="No commands found for container") as excinfo:
        get_command_info("ghcr.io/londonaicentre/xnat-dcm2niix:v1.0.20260724", headers)

    message = str(excinfo.value)
    assert "unexpected response body" in message
    assert "may not be installed" not in message


@patch("imaging_api.services.projects.requests.get")
def test_get_command_info_exact_image_match_drops_the_stale_pin_advice(mock_get, headers):
    """Re-registering cannot help when the requested image is already registered.

    The filtered lookup returning nothing while the unfiltered listing shows the same image means
    the lookup is at fault, not the registration.
    """
    container = "ghcr.io/londonaicentre/xnat-dcm2niix:v1.0.20260724"
    filtered = MagicMock(status_code=200, json=MagicMock(return_value=[]))
    unfiltered = MagicMock(
        status_code=200,
        json=MagicMock(return_value=[{"id": 6, "name": "dcm2niix", "image": container}]),
    )
    mock_get.side_effect = [filtered, unfiltered]

    with pytest.raises(Exception, match="No commands found for container") as excinfo:
        get_command_info(container, headers)

    message = str(excinfo.value)
    assert "that exact image is registered" in message
    assert "configure-dcm2niix.sh" not in message
    assert "predates the current pin" not in message


@patch("imaging_api.services.projects.requests.get")
@pytest.mark.parametrize(
    "image_for",
    [
        lambda i: f"ghcr.io/example/tool-{i:02d}:v1",
        # The shape an operator actually has. An item cap sized against the short fixture above
        # lets ten of these render ~1075 characters, putting the remediation past the hub-side cut.
        lambda i: f"ghcr.io/londonaicentre/xnat-dcm2niix-variant-{i:02d}:v1.0.20260724",
    ],
    ids=["short-images", "realistic-long-images"],
)
def test_get_command_info_listing_stays_inside_the_truncation_budget(mock_get, headers, image_for):
    """The raised string reaches the hub through trust-api's 1000-character truncation.

    The budget has to be in characters, not items: the invariant that matters is that the
    remediation sentence survives the cut, and that is a property of the rendered length, not of
    how many pairs were appended. Parametrised over a short and a realistic image shape so the
    bound is held by the code rather than by the brevity of a fixture.
    """
    filtered = MagicMock(status_code=200, json=MagicMock(return_value=[]))
    many = [{"id": i, "name": f"tool-{i:02d}", "image": image_for(i)} for i in range(40)]
    unfiltered = MagicMock(status_code=200, json=MagicMock(return_value=many))
    mock_get.side_effect = [filtered, unfiltered]

    with pytest.raises(Exception, match="No commands found for container") as excinfo:
        get_command_info("ghcr.io/londonaicentre/xnat-dcm2niix:v1.0.20260724", headers)

    message = str(excinfo.value)
    # The whole message survives the truncation, remediation included - the point of the budget.
    assert len(message) < 1000
    assert "configure-dcm2niix.sh" in message
    assert message.endswith("image is current.")
    # The count still reports the whole registry, and the tail names what was dropped.
    assert "40 command(s) registered" in message
    assert "more" in message
    # Whatever was listed was listed whole - never cut mid-pair.
    listed = message.split("registered: ", 1)[1].split(". If one of those", 1)[0]
    shown = [p for p in listed.split(", ") if p.startswith("'")]
    assert shown, "at least one pair should fit"
    for pair in shown:
        assert pair.endswith("'"), f"pair rendered incomplete: {pair!r}"


@patch("imaging_api.services.projects.requests.get")
def test_get_command_info_forbidden_listing_points_at_the_role_not_credentials(mock_get, headers):
    """403 shares this branch with 401 but has a different documented remedy.

    Container Service 3.7.0+ answers /xapi/commands with 401/403 when the caller lacks the
    ContainerManager role (TROUBLESHOOTING 2.3a); the fix is a role grant. Sending an operator to
    re-check credentials that are working is the same misdirection FLIP#1093 is about.
    """
    filtered = MagicMock(status_code=200, json=MagicMock(return_value=[]))
    forbidden = MagicMock(status_code=403, text="<html>forbidden</html>")
    mock_get.side_effect = [filtered, forbidden]

    with pytest.raises(Exception, match="No commands found for container") as excinfo:
        get_command_info("ghcr.io/londonaicentre/xnat-dcm2niix:v1.0.20260724", headers)

    message = str(excinfo.value)
    assert "HTTP 403" in message
    assert "ContainerManager" in message
    assert "2.3a" in message
    assert "may not be installed" not in message
    # The 401 remedy must not be what a 403 is told to do.
    assert "credentials are wrong" not in message


@patch("imaging_api.services.projects.requests.get")
def test_get_command_info_listing_failure_never_carries_the_html_body(mock_get, headers):
    """XNAT's 401 page is ~683 characters of Tomcat boilerplate.

    It must not travel to the hub inside the raised message, where it would consume most of the
    1000-character budget; the body belongs in the trust-side log.
    """
    filtered = MagicMock(status_code=200, json=MagicMock(return_value=[]))
    unauthorised = MagicMock(status_code=401, text="<html><body>" + "x" * 2000 + "</body></html>")
    mock_get.side_effect = [filtered, unauthorised]

    with pytest.raises(Exception, match="No commands found for container") as excinfo:
        get_command_info("ghcr.io/londonaicentre/xnat-dcm2niix:v1.0.20260724", headers)

    message = str(excinfo.value)
    assert "xxxx" not in message
    assert "<html>" not in message
    assert len(message) < 1000


@patch("imaging_api.services.projects.requests.get")
def test_get_command_info_exact_match_is_listed_even_beyond_the_budget(mock_get, headers):
    """The claim and its evidence must not disagree.

    This branch asserts the exact image *is* registered. Ordered by name, a matching entry sorting
    late would be trimmed away by the budget, leaving a message that asserts a registration and
    shows a listing without it - the claim-without-support shape this helper exists to remove.
    """
    target = "ghcr.io/londonaicentre/xnat-dcm2niix:v1.0.20260724"
    filtered = MagicMock(status_code=200, json=MagicMock(return_value=[]))
    many = [{"id": i, "name": f"tool-{i:02d}", "image": f"ghcr.io/example/tool-{i:02d}:v1"} for i in range(40)]
    # Sorts last by name, so a naive name-ordered trim would drop it.
    many.append({"id": 99, "name": "zzz-dcm2niix", "image": target})
    unfiltered = MagicMock(status_code=200, json=MagicMock(return_value=many))
    mock_get.side_effect = [filtered, unfiltered]

    with pytest.raises(Exception, match="No commands found for container") as excinfo:
        get_command_info(target, headers)

    message = str(excinfo.value)
    assert "that exact image is registered" in message
    assert f"'zzz-dcm2niix' -> '{target}'" in message, "the entry the message claims is registered must be shown"
    assert len(message) < 1000


@patch("imaging_api.services.projects.requests.get")
@pytest.mark.parametrize(
    "command",
    [
        {"id": 1, "name": "dcm2niix"},
        {"id": 1, "name": "dcm2niix", "xnat": []},
        {"id": 1, "name": "dcm2niix", "xnat": [{}]},
    ],
    ids=["no-xnat-key", "empty-wrapper-list", "wrapper-without-name"],
)
def test_get_command_info_wrapperless_command_is_a_legible_error(mock_get, headers, command):
    """The mismatch message teaches "a command with no 'xnat' wrapper" as a hypothesis to check.

    Unguarded, that exact state raises a bare KeyError('xnat') - or IndexError on an empty wrapper
    list - and reaches the hub as a task error with nothing in it, which is the opaque failure class
    this change is narrowing. 2.3a's verification step checks for the wrapper, so it is a real state.
    """
    mock_get.return_value = MagicMock(status_code=200, json=MagicMock(return_value=[command]))

    with pytest.raises(Exception, match="no 'xnat' wrapper") as excinfo:
        get_command_info("ghcr.io/londonaicentre/xnat-dcm2niix:v1.0.20260724", headers)

    assert not isinstance(excinfo.value, KeyError | IndexError), "must not surface as a bare lookup error"
    message = str(excinfo.value)
    assert "no 'xnat' wrapper" in message
    assert "dcm2niix" in message
    assert "configure-dcm2niix.sh" in message


@patch("imaging_api.services.projects.requests.get")
def test_get_command_info_fetch_failure_bounds_the_error_body(mock_get, headers):
    """Wrong credentials fail on the filtered lookup, before the diagnostic helper is reached.

    XNAT answers with a whole Tomcat error page; interpolating it whole leaves the hub-side error
    almost entirely login-page markup, and subject to the same truncation the message below
    respects. Bound it here and keep the full body in the log.
    """
    mock_get.return_value = MagicMock(status_code=401, text="<html>" + "y" * 5000 + "</html>")

    with pytest.raises(Exception, match="XNAT command fetch failed") as excinfo:
        get_command_info("ghcr.io/londonaicentre/xnat-dcm2niix:v1.0.20260724", headers)

    message = str(excinfo.value)
    assert "401" in message
    assert len(message) < 1000
    assert "more characters, full body in the imaging-api log" in message


# ===========================================================================
# create_project_event_subscription
# ===========================================================================
@patch("imaging_api.services.projects.requests.post")
@patch("imaging_api.services.projects.requests.put")
@patch("imaging_api.services.projects.get_command_info")
def test_create_project_event_subscription_active(mock_cmd_info, mock_put, mock_post, headers):
    mock_cmd_info.return_value = (1, "dcm2niix-scan")
    mock_put.return_value = MagicMock(status_code=200)
    mock_post.return_value = MagicMock(status_code=200)

    create_project_event_subscription("TEST", "ghcr.io/londonaicentre/xnat-dcm2niix:v1.0.20260724", True, headers)

    mock_put.assert_called_once()
    assert "/commands/1/wrappers/dcm2niix-scan/enabled" in mock_put.call_args[0][0]
    mock_post.assert_called_once()
    call_url = mock_post.call_args[0][0]
    call_payload = mock_post.call_args[1]["json"]
    assert "/xapi/projects/TEST/events/subscription" in call_url
    assert call_payload["active"] is True
    assert "CommandActionProvider:1" in call_payload["action-key"]


@patch("imaging_api.services.projects.requests.post")
@patch("imaging_api.services.projects.requests.put")
@patch("imaging_api.services.projects.get_command_info")
def test_create_project_event_subscription_inactive(mock_cmd_info, mock_put, mock_post, headers):
    mock_cmd_info.return_value = (1, "dcm2niix-scan")
    mock_put.return_value = MagicMock(status_code=200)
    mock_post.return_value = MagicMock(status_code=200)

    create_project_event_subscription("TEST", "ghcr.io/londonaicentre/xnat-dcm2niix:v1.0.20260724", False, headers)

    mock_post.assert_called_once()
    call_payload = mock_post.call_args[1]["json"]
    assert call_payload["active"] is False


@patch("imaging_api.services.projects.requests.put")
@patch("imaging_api.services.projects.get_command_info")
def test_create_project_event_subscription_enable_command_failure(mock_cmd_info, mock_put, headers):
    mock_cmd_info.return_value = (1, "dcm2niix-scan")
    mock_put.return_value = MagicMock(status_code=500, text="Internal Server Error")

    with pytest.raises(Exception, match="Enabling command"):
        create_project_event_subscription("TEST", "ghcr.io/londonaicentre/xnat-dcm2niix:v1.0.20260724", True, headers)


@patch("imaging_api.services.projects.requests.post")
@patch("imaging_api.services.projects.requests.put")
@patch("imaging_api.services.projects.get_command_info")
def test_create_project_event_subscription_failure(mock_cmd_info, mock_put, mock_post, headers):
    mock_cmd_info.return_value = (1, "dcm2niix-scan")
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


@patch("imaging_api.services.projects.add_user_to_project")
@patch("imaging_api.services.projects.get_user_profile_by")
def test_add_central_hub_users_existing_user(mock_get_profile, mock_add, headers):
    user_profile = User(**_USER_DICT)
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
    assert len(created) == 0
    assert len(added) == 1


@patch("imaging_api.services.projects.add_user_to_project")
@patch("imaging_api.services.projects.create_user_from_central_hub_user")
@patch("imaging_api.services.projects.get_user_profile_by")
def test_add_central_hub_users_new_user(mock_get_profile, mock_create, mock_add, headers):
    mock_get_profile.side_effect = NotFoundError("not found")
    user_profile = User(**_USER_DICT)
    created_user = CreatedUser(username="alice", encrypted_password="enc", email="alice@test.com")
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
    assert len(created) == 1
    assert len(added) == 1


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
        status_code=500, text="Error", json=MagicMock(side_effect=ValueError("no json")),
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
