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
from uuid import UUID

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient

from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.domain.interfaces.project import ProjectStatus
from flip_api.domain.schemas.projects import StageProjectRequest
from flip_api.project_services.stage_project import router as stage_project_router

# Global test data
TEST_PROJECT_ID = uuid.uuid4()
TEST_USER_ID = uuid.uuid4()


@pytest.fixture
def app_fixture() -> FastAPI:
    app = FastAPI()
    app.include_router(stage_project_router)
    return app


@pytest.fixture
def client(app_fixture: FastAPI) -> TestClient:
    return TestClient(app_fixture)


def mock_verify_token():
    return TEST_USER_ID


@pytest.fixture
def test_user_id():
    return UUID("12345678-1234-5678-9012-123456789012")


@pytest.fixture
def test_project_id():
    return UUID("87654321-4321-8765-2109-876543210987")


@pytest.fixture
def stage_request_payload():
    stage_request = StageProjectRequest(
        trusts=[UUID("11111111-1111-1111-1111-111111111111"), UUID("22222222-2222-2222-2222-222222222222")]
    )
    return stage_request.model_dump(mode="json")


@pytest.fixture
def mock_project_data():
    mock_project = MagicMock()
    mock_project.status = ProjectStatus.UNSTAGED.value
    mock_project.query = MagicMock()
    mock_project.query.trusts_queried = 5
    return mock_project


def test_stage_project_success(
    app_fixture, client, test_user_id, test_project_id, stage_request_payload, mock_project_data
):
    # Arrange
    mock_session = MagicMock()
    app_fixture.dependency_overrides[get_session] = lambda: mock_session
    app_fixture.dependency_overrides[verify_token] = lambda: test_user_id

    with (
        patch("flip_api.project_services.stage_project.can_access_project", return_value=True),
        patch("flip_api.project_services.stage_project.get_project", return_value=mock_project_data),
        patch("flip_api.project_services.stage_project.stage_project_service"),
    ):
        # Act
        response = client.post(f"/projects/{test_project_id}/stage", json=stage_request_payload)

    # Assert
    assert response.status_code == status.HTTP_204_NO_CONTENT


def test_stage_project_access_denied(app_fixture, client, test_user_id, test_project_id, stage_request_payload):
    # Arrange
    mock_session = MagicMock()
    app_fixture.dependency_overrides[get_session] = lambda: mock_session
    app_fixture.dependency_overrides[verify_token] = lambda: test_user_id

    with (
        patch("flip_api.project_services.stage_project.can_access_project", return_value=False),
        patch("flip_api.project_services.stage_project.get_project") as mock_get_project,
    ):
        # Act
        response = client.post(f"/projects/{test_project_id}/stage", json=stage_request_payload)

    # Assert
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["detail"] == f"User with ID: {test_user_id} is not allowed to stage this project."
    mock_get_project.assert_not_called()


def test_stage_project_not_found(app_fixture, client, test_user_id, test_project_id, stage_request_payload):
    # Arrange
    mock_session = MagicMock()
    app_fixture.dependency_overrides[get_session] = lambda: mock_session
    app_fixture.dependency_overrides[verify_token] = lambda: test_user_id

    with (
        patch("flip_api.project_services.stage_project.can_access_project", return_value=True),
        patch("flip_api.project_services.stage_project.get_project", return_value=None),
    ):
        # Act
        response = client.post(f"/projects/{test_project_id}/stage", json=stage_request_payload)

    # Assert
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["detail"] == f"Unable to find project with ID: {test_project_id}"


def test_stage_project_not_unstaged_status(app_fixture, client, test_user_id, test_project_id, stage_request_payload):
    # Arrange
    mock_session = MagicMock()
    app_fixture.dependency_overrides[get_session] = lambda: mock_session
    app_fixture.dependency_overrides[verify_token] = lambda: test_user_id

    mock_project_data = MagicMock()
    mock_project_data.status = ProjectStatus.STAGED

    with (
        patch("flip_api.project_services.stage_project.can_access_project", return_value=True),
        patch("flip_api.project_services.stage_project.get_project", return_value=mock_project_data),
    ):
        # Act
        response = client.post(f"/projects/{test_project_id}/stage", json=stage_request_payload)

    # Assert
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert (
        response.json()["detail"]
        == f"Project with ID: {test_project_id} is not 'ProjectStatus.UNSTAGED' and cannot be staged."
    )


def test_stage_project_no_query(app_fixture, client, test_user_id, test_project_id, stage_request_payload):
    # Arrange
    mock_session = MagicMock()
    app_fixture.dependency_overrides[get_session] = lambda: mock_session
    app_fixture.dependency_overrides[verify_token] = lambda: test_user_id

    mock_project_data = MagicMock()
    mock_project_data.status = ProjectStatus.UNSTAGED.value
    mock_project_data.query = None

    with (
        patch("flip_api.project_services.stage_project.can_access_project", return_value=True),
        patch("flip_api.project_services.stage_project.get_project", return_value=mock_project_data),
    ):
        # Act
        response = client.post(f"/projects/{test_project_id}/stage", json=stage_request_payload)

    # Assert
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    expected_detail = (
        f"Project with ID: {test_project_id} does not have a valid cohort query with trusts queried and"
        " cannot be staged."
    )
    assert response.json()["detail"] == expected_detail


def test_stage_project_invalid_trusts_queried(
    app_fixture, client, test_user_id, test_project_id, stage_request_payload
):
    # Arrange
    mock_session = MagicMock()
    app_fixture.dependency_overrides[get_session] = lambda: mock_session
    app_fixture.dependency_overrides[verify_token] = lambda: test_user_id

    mock_project_data = MagicMock()
    mock_project_data.status = ProjectStatus.UNSTAGED.value
    mock_project_data.query = MagicMock()
    mock_project_data.query.trusts_queried = 0

    with (
        patch("flip_api.project_services.stage_project.can_access_project", return_value=True),
        patch("flip_api.project_services.stage_project.get_project", return_value=mock_project_data),
    ):
        # Act
        response = client.post(f"/projects/{test_project_id}/stage", json=stage_request_payload)

    # Assert
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    expected_detail = (
        f"Project with ID: {test_project_id} does not have a valid cohort query with trusts queried and "
        "cannot be staged."
    )
    assert response.json()["detail"] == expected_detail


def test_stage_project_value_error_from_service(
    app_fixture, client, test_user_id, test_project_id, stage_request_payload, mock_project_data
):
    # Arrange
    mock_session = MagicMock()
    app_fixture.dependency_overrides[get_session] = lambda: mock_session
    app_fixture.dependency_overrides[verify_token] = lambda: test_user_id

    with (
        patch("flip_api.project_services.stage_project.can_access_project", return_value=True),
        patch("flip_api.project_services.stage_project.get_project", return_value=mock_project_data),
        patch(
            "flip_api.project_services.stage_project.stage_project_service",
            side_effect=ValueError("Invalid trust configuration"),
        ),
    ):
        # Act
        response = client.post(f"/projects/{test_project_id}/stage", json=stage_request_payload)

    # Assert
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["detail"] == "Invalid trust configuration"


def test_stage_project_generic_exception(
    app_fixture, client, test_user_id, test_project_id, stage_request_payload, mock_project_data
):
    # Arrange
    mock_session = MagicMock()
    app_fixture.dependency_overrides[get_session] = lambda: mock_session
    app_fixture.dependency_overrides[verify_token] = lambda: test_user_id

    with (
        patch("flip_api.project_services.stage_project.can_access_project", return_value=True),
        patch("flip_api.project_services.stage_project.get_project", return_value=mock_project_data),
        patch(
            "flip_api.project_services.stage_project.stage_project_service", side_effect=Exception("Database error")
        ),
    ):
        # Act
        response = client.post(f"/projects/{test_project_id}/stage", json=stage_request_payload)

    # Assert
    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert response.json()["detail"] == "An unexpected error occurred while staging the project."


def test_stage_project_invalid_project_id_format(app_fixture, client, stage_request_payload):
    # Arrange
    mock_session = MagicMock()
    app_fixture.dependency_overrides[get_session] = lambda: mock_session
    app_fixture.dependency_overrides[verify_token] = lambda: UUID("12345678-1234-5678-9012-123456789012")

    invalid_project_id = "not-a-valid-uuid"

    # Act
    response = client.post(f"/projects/{invalid_project_id}/stage", json=stage_request_payload)

    # Assert
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    # FastAPI automatically validates UUID path parameters and returns 422 for invalid formats


def test_stage_project_missing_trusts_in_payload(app_fixture, client, test_user_id, test_project_id):
    # Arrange
    mock_session = MagicMock()
    app_fixture.dependency_overrides[get_session] = lambda: mock_session
    app_fixture.dependency_overrides[verify_token] = lambda: test_user_id

    invalid_payload = {}  # Missing trusts field

    # Act
    response = client.post(f"/projects/{test_project_id}/stage", json=invalid_payload)

    # Assert
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    # FastAPI automatically validates request body and returns 422 for missing required fields
