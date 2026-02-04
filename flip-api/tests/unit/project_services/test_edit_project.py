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
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient

from flip_api.auth.dependencies import verify_token
from flip.db.database import get_session
from flip.db.models.main_models import Projects
from flip.project_services.edit_project import router as edit_project_router

# Common test data
TEST_PROJECT_ID = uuid.uuid4()


@pytest.fixture
def app_fixture() -> FastAPI:
    app = FastAPI()
    app.include_router(edit_project_router)
    return app


@pytest.fixture
def client(app_fixture: FastAPI) -> TestClient:
    return TestClient(app_fixture)


@pytest.fixture
def mock_edit_payload():
    return {"name": "New Name", "description": "New Desc"}


@pytest.fixture
def mock_get_user_pool_id():
    with patch("flip.project_services.edit_project.get_user_pool_id", return_value="mock_user_pool_id"):
        yield


@pytest.fixture
def mock_filter_enabled_users():
    with patch(
        "flip.project_services.edit_project.filter_enabled_users", return_value=[uuid.uuid4(), uuid.uuid4()]
    ):
        yield


def test_edit_project_success(
    client: TestClient, app_fixture: FastAPI, mock_edit_payload, mock_get_user_pool_id, mock_filter_enabled_users
):
    mock_db_session = MagicMock()
    # Simulate an existing project
    mock_project_instance = Projects(id=TEST_PROJECT_ID, name="Old Name", description="Old Desc")
    mock_db_session.get.return_value = mock_project_instance

    app_fixture.dependency_overrides[get_session] = lambda: mock_db_session
    app_fixture.dependency_overrides[verify_token] = lambda: uuid.uuid4()

    with patch("flip.project_services.edit_project.can_access_project", return_value=True) as mock_can_access:
        response = client.put(
            f"/projects/{str(TEST_PROJECT_ID)}",
            json=mock_edit_payload,
        )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["name"] == "New Name"
    assert data["description"] == "New Desc"

    mock_can_access.assert_called_once()
    mock_db_session.get.assert_called_with(Projects, TEST_PROJECT_ID)
    assert mock_project_instance.name == "New Name"
    assert mock_project_instance.description == "New Desc"
    mock_db_session.commit.assert_called_once()

    app_fixture.dependency_overrides = {}


def test_edit_project_no_permission(client: TestClient, app_fixture: FastAPI, mock_edit_payload):
    mock_db_session = MagicMock()
    app_fixture.dependency_overrides[get_session] = lambda: mock_db_session
    app_fixture.dependency_overrides[verify_token] = lambda: uuid.uuid4()

    with patch("flip.project_services.edit_project.can_access_project", return_value=False) as mock_can_access:
        response = client.put(
            f"/projects/{str(TEST_PROJECT_ID)}",
            json=mock_edit_payload,
        )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert "is not allowed to edit this project" in response.json()["detail"]
    mock_can_access.assert_called_once()
    mock_db_session.commit.assert_not_called()
    app_fixture.dependency_overrides = {}


def test_edit_project_not_found(client: TestClient, app_fixture: FastAPI, mock_edit_payload):
    mock_db_session = MagicMock()
    mock_db_session.get.return_value = None  # Simulate project not found

    project_id = uuid.uuid4()  # Use a random UUID for the non-existent project

    app_fixture.dependency_overrides[get_session] = lambda: mock_db_session
    app_fixture.dependency_overrides[verify_token] = lambda: uuid.uuid4()

    with patch("flip.project_services.edit_project.can_access_project", return_value=True) as mock_can_access:
        response = client.put(
            f"/projects/{project_id}",
            json=mock_edit_payload,
        )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["detail"] == f"Project with ID {project_id} does not exist or is deleted, cannot edit."
    mock_can_access.assert_called_once()
    mock_db_session.commit.assert_not_called()
    app_fixture.dependency_overrides = {}


def test_edit_project_staged(client: TestClient, app_fixture: FastAPI, mock_edit_payload):
    mock_db_session = MagicMock()
    # Simulate an existing project
    mock_project_instance = Projects(id=TEST_PROJECT_ID, name="Old Name", description="Old Desc", status="STAGED")
    mock_db_session.get.return_value = mock_project_instance

    app_fixture.dependency_overrides[get_session] = lambda: mock_db_session
    app_fixture.dependency_overrides[verify_token] = lambda: uuid.uuid4()

    with patch("flip.project_services.edit_project.can_access_project", return_value=True) as mock_can_access:
        response = client.put(
            f"/projects/{str(TEST_PROJECT_ID)}",
            json=mock_edit_payload,
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "Unable to edit the project as it has already been staged/approved" in response.json()["detail"]

    mock_can_access.assert_called_once()
    mock_db_session.get.assert_called_with(Projects, TEST_PROJECT_ID)
    mock_db_session.commit.assert_not_called()

    app_fixture.dependency_overrides = {}


def test_edit_project_db_commit_value_error(
    client: TestClient, app_fixture: FastAPI, mock_edit_payload, mock_get_user_pool_id, mock_filter_enabled_users
):
    mock_db_session = MagicMock()
    mock_project_instance = Projects(id=TEST_PROJECT_ID, name="Old Name", description="Old Desc")
    mock_db_session.get.return_value = mock_project_instance
    mock_db_session.commit.side_effect = ValueError("DB commit failed")

    app_fixture.dependency_overrides[get_session] = lambda: mock_db_session
    app_fixture.dependency_overrides[verify_token] = lambda: uuid.uuid4()

    with patch("flip.project_services.edit_project.can_access_project", return_value=True) as mock_can_access:
        response = client.put(
            f"/projects/{str(TEST_PROJECT_ID)}",
            json=mock_edit_payload,
        )

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert "DB commit failed" in response.json()["detail"]
    mock_can_access.assert_called_once()
    mock_db_session.rollback.assert_called_once()
    app_fixture.dependency_overrides = {}


def test_edit_project_db_commit_generic_exception(
    client: TestClient, app_fixture: FastAPI, mock_edit_payload, mock_get_user_pool_id, mock_filter_enabled_users
):
    mock_db_session = MagicMock()
    mock_project_instance = Projects(id=TEST_PROJECT_ID, name="Old Name", description="Old Desc")
    mock_db_session.get.return_value = mock_project_instance
    mock_db_session.commit.side_effect = Exception("Unexpected DB error")

    app_fixture.dependency_overrides[get_session] = lambda: mock_db_session
    app_fixture.dependency_overrides[verify_token] = lambda: uuid.uuid4()

    with patch("flip.project_services.edit_project.can_access_project", return_value=True) as mock_can_access:
        response = client.put(
            f"/projects/{str(TEST_PROJECT_ID)}",
            json=mock_edit_payload,
        )

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert "Unexpected DB error" in response.json()["detail"]
    mock_can_access.assert_called_once()
    mock_db_session.rollback.assert_called_once()
    app_fixture.dependency_overrides = {}
