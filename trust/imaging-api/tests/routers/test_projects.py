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

import inspect
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from imaging_api.config import get_settings
from imaging_api.routers.schemas import CentralHubProject, Experiment, Project, Subject
from imaging_api.utils.exceptions import AlreadyExistsError, NotFoundError

TEST_XNAT_PROJECT_ID = "dc5c1758-1a4d-4fca-80ce-fa208d11874d"


@pytest.fixture
def mock_project():
    return Project(
        ID=TEST_XNAT_PROJECT_ID,
        secondary_ID="TEST",
        name="Test Project",
        description="A test project",
        pi_firstname="John",
        pi_lastname="Doe",
        URI=f"/projects/{TEST_XNAT_PROJECT_ID}",
    )


_CREATE_PARAMS = {"project_id": "PROJ1", "project_secondary_id": "hub-123", "project_name": "Test Project"}

_SAMPLE_PROJECT = Project(
    pi_firstname="John",
    secondary_ID="hub-123",
    pi_lastname="Doe",
    name="Test Project",
    description="A test project",
    ID="PROJ1",
    URI="/data/projects/PROJ1",
)


# ── Event subscription tests (async, direct function call) ──


@pytest.mark.asyncio
@pytest.mark.parametrize("dicom_to_nifti", [True, False])
@patch("imaging_api.routers.projects.retrieve_images_for_project")
@patch("imaging_api.routers.projects.add_central_hub_users_to_project")
@patch("imaging_api.routers.projects.create_project_event_subscription")
@patch("imaging_api.routers.projects.set_project_prearchive_settings")
@patch("imaging_api.routers.projects.create_project")
@patch("imaging_api.routers.projects.to_create_project")
@patch("imaging_api.routers.projects.get_xnat_auth_headers")
async def test_create_project_event_subscription_active_matches_dicom_to_nifti(
    mock_auth,
    mock_to_create,
    mock_create,
    mock_prearchive,
    mock_event_sub,
    mock_add_users,
    mock_retrieve,
    mock_project,
    dicom_to_nifti,
):
    """create_project_event_subscription active flag should match dicom_to_nifti."""
    mock_to_create.return_value = MagicMock(id="TEST", secondary_id="TEST", name="Test", description="")
    mock_create.return_value = mock_project
    mock_add_users.return_value = ([], [])

    central_hub_project = CentralHubProject(
        project_id=uuid4(),
        trust_id=uuid4(),
        project_name="Test Project",
        query="SELECT * FROM studies",
        users=[],
        dicom_to_nifti=dicom_to_nifti,
    )

    from imaging_api.routers.projects import create_project_from_central_hub_project

    headers = {"Cookie": "JSESSIONID=mock"}
    background_tasks = MagicMock()
    create_project_from_central_hub_project(central_hub_project, headers, background_tasks)

    mock_event_sub.assert_called_once_with(TEST_XNAT_PROJECT_ID, get_settings().DCM2NIIX_IMAGE, dicom_to_nifti, headers)


def test_create_project_from_central_hub_project_runs_in_the_threadpool():
    """Its XNAT calls block; as a coroutine they would stall the single event loop, /health included."""
    from imaging_api.routers.projects import create_project_from_central_hub_project

    assert not inspect.iscoroutinefunction(create_project_from_central_hub_project)


# ── GET /projects/ ──


def test_get_projects_success(client):
    with patch("imaging_api.routers.projects.get_all_projects", return_value=[_SAMPLE_PROJECT]):
        response = client.get("/projects/")

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["ID"] == "PROJ1"


# ── POST /projects/ ──


def test_create_project_success(client):
    with patch("imaging_api.routers.projects.create_project", return_value=_SAMPLE_PROJECT):
        response = client.post("/projects/", params=_CREATE_PARAMS)

    assert response.status_code == 200
    assert response.json()["ID"] == "PROJ1"


# ── DELETE /projects/{project_id} ──


def test_delete_project_success(client):
    with patch(
        "imaging_api.routers.projects.delete_project",
        new_callable=AsyncMock,
        return_value=_SAMPLE_PROJECT,
    ):
        response = client.delete("/projects/PROJ1")

    assert response.status_code == 200


# ── GET /projects/{project_id} ──


def test_get_project_success(client):
    with patch("imaging_api.routers.projects.get_project", return_value=_SAMPLE_PROJECT):
        response = client.get("/projects/PROJ1")

    assert response.status_code == 200
    assert response.json()["ID"] == "PROJ1"


# ── GET /projects/{project_id}/subjects ──


def test_get_subjects_success(client):
    sample_subject = Subject(
        ID="SUBJ1",
        label="Subject 1",
        insert_date="2024-01-01",
        project="PROJ1",
        insert_user="admin",
        URI="/data/subjects/SUBJ1",
    )
    with patch("imaging_api.routers.projects.get_subjects", return_value=[sample_subject]):
        response = client.get("/projects/PROJ1/subjects")

    assert response.status_code == 200
    assert len(response.json()) == 1


# ── GET /projects/{project_id}/experiments ──


def test_get_experiments_success(client):
    sample_experiment = Experiment(
        ID="EXP1",
        label="Experiment 1",
        date="2024-01-01",
        project="PROJ1",
        insert_date="2024-01-01",
        xsiType="xnat:mrSessionData",
        URI="/data/experiments/EXP1",
    )
    with patch("imaging_api.routers.projects.get_experiments", return_value=[sample_experiment]):
        response = client.get("/projects/PROJ1/experiments")

    assert response.status_code == 200
    assert len(response.json()) == 1


# ── GET /projects/{project_id}/experiment/{experiment_id_or_label} ──


def test_get_experiment_success(client):
    mock_data = {"items": [{"data_fields": {"ID": "EXP1"}}]}
    with patch("imaging_api.routers.projects.get_experiment", return_value=mock_data):
        response = client.get("/projects/PROJ1/experiment/EXP1")

    assert response.status_code == 200


# ── Service errors map to HTTP status ──

_ERROR_CASES = [
    pytest.param("get_all_projects", "GET", "/projects/", Exception("connection error"), 500, id="get_projects_error"),
    pytest.param(
        "create_project",
        "POST",
        "/projects/",
        AlreadyExistsError("Project already exists"),
        400,
        id="create_project_already_exists",
    ),
    pytest.param("create_project", "POST", "/projects/", Exception("XNAT error"), 500, id="create_project_error"),
    pytest.param(
        "delete_project",
        "DELETE",
        "/projects/BAD_PROJ",
        NotFoundError("Project not found"),
        404,
        id="delete_project_not_found",
    ),
    pytest.param(
        "delete_project", "DELETE", "/projects/PROJ1", Exception("XNAT error"), 500, id="delete_project_error"
    ),
    pytest.param(
        "get_project", "GET", "/projects/BAD_PROJ", NotFoundError("Not found"), 404, id="get_project_not_found"
    ),
    pytest.param("get_project", "GET", "/projects/PROJ1", Exception("error"), 500, id="get_project_error"),
    pytest.param(
        "get_subjects",
        "GET",
        "/projects/BAD_PROJ/subjects",
        NotFoundError("Not found"),
        404,
        id="get_subjects_not_found",
    ),
    pytest.param("get_subjects", "GET", "/projects/PROJ1/subjects", Exception("error"), 500, id="get_subjects_error"),
    pytest.param(
        "get_experiments",
        "GET",
        "/projects/BAD_PROJ/experiments",
        NotFoundError("Not found"),
        404,
        id="get_experiments_not_found",
    ),
    pytest.param(
        "get_experiments", "GET", "/projects/PROJ1/experiments", Exception("error"), 500, id="get_experiments_error"
    ),
    pytest.param(
        "get_experiment",
        "GET",
        "/projects/PROJ1/experiment/BAD_EXP",
        NotFoundError("Not found"),
        404,
        id="get_experiment_not_found",
    ),
    pytest.param(
        "get_experiment", "GET", "/projects/PROJ1/experiment/EXP1", Exception("error"), 500, id="get_experiment_error"
    ),
]


@pytest.mark.parametrize(("target", "method", "path", "exc", "status"), _ERROR_CASES)
def test_service_error_maps_to_status(client, target, method, path, exc, status):
    # Same patch() as before: only delete_project (awaited by the router) is an explicit AsyncMock.
    extra = {"new_callable": AsyncMock} if target == "delete_project" else {}
    with patch(f"imaging_api.routers.projects.{target}", side_effect=exc, **extra):
        response = client.request(method, path, params=_CREATE_PARAMS if method == "POST" else None)

    assert response.status_code == status
