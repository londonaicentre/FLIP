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
    delete_project,
    delete_queued_import_requests,
    enable_project_command,
    get_all_projects,
    get_experiment,
    get_experiments,
    get_project,
    get_project_from_central_hub_project_id,
    get_subject_id_from_experiment_response,
    get_subjects,
    set_project_prearchive_settings,
    to_create_project,
)
from imaging_api.utils.exceptions import AlreadyExistsError, NotFoundError


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
        "http://xnat/projects", "P1", "S1", "My Project", "A description",
    )
    assert "<ID>P1</ID>" in payload
    assert "<secondary_ID>S1</secondary_ID>" in payload
    assert "<name>My Project</name>" in payload
    assert "<description>A description</description>" in payload


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
        project_id=uuid4(), trust_id=uuid4(), project_name="My Project", query="SELECT *",
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
# enable_project_command
# ===========================================================================
@patch("imaging_api.services.projects.requests.put")
@patch("imaging_api.services.projects.requests.get")
def test_enable_project_command_success(mock_get, mock_put, headers):
    mock_get.return_value = MagicMock(
        status_code=200,
        json=MagicMock(return_value=[
            {"id": 42, "xnat": [{"name": "dcm2niix-wrapper"}]},
        ]),
    )
    mock_put.return_value = MagicMock(status_code=200)

    enable_project_command("TEST", "xnat/dcm2niix:latest", headers)


@patch("imaging_api.services.projects.requests.get")
def test_enable_project_command_get_failure(mock_get, headers):
    mock_get.return_value = MagicMock(status_code=500, text="Error")
    with pytest.raises(Exception, match="XNAT command fetch failed"):
        enable_project_command("TEST", "xnat/dcm2niix:latest", headers)


@patch("imaging_api.services.projects.requests.put")
@patch("imaging_api.services.projects.requests.get")
def test_enable_project_command_put_failure(mock_get, mock_put, headers):
    mock_get.return_value = MagicMock(
        status_code=200,
        json=MagicMock(return_value=[
            {"id": 42, "xnat": [{"name": "dcm2niix-wrapper"}]},
        ]),
    )
    mock_put.return_value = MagicMock(status_code=500, text="Error")

    with pytest.raises(Exception, match="Enabling XNAT command"):
        enable_project_command("TEST", "xnat/dcm2niix:latest", headers)


# ===========================================================================
# add_central_hub_users_to_project
# ===========================================================================
@patch("imaging_api.services.projects.add_user_to_project")
@patch("imaging_api.services.projects.get_user_profile_by")
def test_add_central_hub_users_no_users(mock_get_profile, mock_add, headers):
    hub_project = CentralHubProject(
        project_id=uuid4(), trust_id=uuid4(), project_name="Proj", query="SELECT *", users=[],
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
        project_id=uuid4(), trust_id=uuid4(), project_name="Proj", query="SELECT *", users=[hub_user],
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
        project_id=uuid4(), trust_id=uuid4(), project_name="Proj", query="SELECT *", users=[hub_user],
    )

    created, added = add_central_hub_users_to_project(hub_project, "TEST", headers)
    assert len(created) == 1
    assert len(added) == 1


@patch("imaging_api.services.projects.add_user_to_project")
@patch("imaging_api.services.projects.get_user_profile_by")
def test_add_central_hub_users_disabled_user_skipped(mock_get_profile, mock_add, headers):
    hub_user = CentralHubUser(id=uuid4(), email="disabled@test.com", is_disabled=True)
    hub_project = CentralHubProject(
        project_id=uuid4(), trust_id=uuid4(), project_name="Proj", query="SELECT *", users=[hub_user],
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
            id=1, created="2023-10-01T00:00:00", accession_number="FAK57777617",
            status="QUEUED", xnat_project="TEST",
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
            id=1, created="2023-10-01T00:00:00", accession_number="FAK57777617",
            status="QUEUED", xnat_project="TEST",
        )
    ]
    mock_post.return_value = MagicMock(status_code=500, text="Error")

    async def fake_session():
        yield MagicMock()

    with patch("imaging_api.services.projects.get_session", side_effect=lambda: fake_session()):
        result = await delete_queued_import_requests("TEST", headers)
    assert result is False


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
        json=MagicMock(return_value={"ResultSet": {"Result": [
            {"ID": "S1", "label": "subj1", "insert_date": "2023-01-01",
             "project": "TEST", "insert_user": "admin", "URI": "/subjects/S1"},
        ]}}),
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
        json=MagicMock(return_value={"ResultSet": {"Result": [
            {"ID": "E1", "label": "exp1", "date": "2023-01-01", "project": "TEST",
             "insert_date": "2023-01-01", "xsiType": "xnat:ctScanData", "URI": "/exp/E1"},
        ]}}),
    )
    experiments = get_experiments("TEST", headers)
    assert len(experiments) == 1
    assert experiments[0].label == "exp1"


@patch("imaging_api.services.projects.requests.get")
@patch("imaging_api.services.projects.get_project")
def test_get_experiments_failure(mock_get_project, mock_get, headers):
    mock_get_project.return_value = Project(**_PROJECT_DICT)
    mock_get.return_value = MagicMock(status_code=500, text="Error")
    with pytest.raises(Exception, match="XNAT experiments fetch failed"):
        get_experiments("TEST", headers)


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
