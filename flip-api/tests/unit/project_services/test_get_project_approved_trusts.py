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
from flip_api.db.database import get_session
from flip_api.db.models.main_models import (
    Projects,
    Trust,
)
from flip_api.domain.schemas.status import ProjectStatus
from flip_api.project_services.get_project_approved_trusts import router as get_project_approved_trusts_router

# Assuming the router and endpoint are in get_project_approved_trusts.py
# Use absolute import based on the project structure

# Global test data
TEST_PROJECT_ID = uuid.uuid4()
TEST_USER_ID = uuid.uuid4()


@pytest.fixture
def app_fixture() -> FastAPI:
    app = FastAPI()
    app.include_router(get_project_approved_trusts_router, prefix="/api")
    return app


@pytest.fixture
def client(app_fixture: FastAPI) -> TestClient:
    return TestClient(app_fixture)


def mock_verify_token():
    return TEST_USER_ID


def test_get_project_approved_trusts_success(
    app_fixture,
    user_id,
    client: TestClient,
    session: MagicMock,
    project_trust_intersect: tuple,
    mock_project: Projects,
):
    # Arrange
    mock_db_session = MagicMock()

    app_fixture.dependency_overrides[get_session] = lambda: mock_db_session
    app_fixture.dependency_overrides[verify_token] = lambda: user_id

    with (
        patch("flip_api.project_services.get_project_approved_trusts.can_access_project", return_value=True),
        patch("flip_api.project_services.get_project_approved_trusts.get_project", return_value=mock_project),
    ):
        response = client.get(f"/api/projects/{TEST_PROJECT_ID}/trusts/approved")

    # Assert
    assert response.status_code == status.HTTP_200_OK


def test_get_project_approved_trusts_forbidden(
    app_fixture,
    client: TestClient,
    session: MagicMock,
):
    # Arrange
    mock_db_session = MagicMock()

    app_fixture.dependency_overrides[get_session] = lambda: mock_db_session
    app_fixture.dependency_overrides[verify_token] = lambda: TEST_USER_ID

    with patch("flip_api.project_services.get_project_approved_trusts.can_access_project", return_value=False):
        response = client.get(f"/api/projects/{TEST_PROJECT_ID}/trusts/approved")

    # Assert
    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_get_project_approved_trusts_not_found(
    app_fixture,
    client: TestClient,
    session: MagicMock,
):
    # Arrange
    mock_db_session = MagicMock()

    app_fixture.dependency_overrides[get_session] = lambda: mock_db_session
    app_fixture.dependency_overrides[verify_token] = lambda: TEST_USER_ID

    with (
        patch("flip_api.project_services.get_project_approved_trusts.can_access_project", return_value=True),
        patch("flip_api.project_services.get_project_approved_trusts.get_project", return_value=None),
    ):
        response = client.get(f"/api/projects/{TEST_PROJECT_ID}/trusts/approved")

    # Assert
    assert response.status_code == status.HTTP_404_NOT_FOUND

    # Note: The imports above (ProjectStatus, Trust, Projects) might need to be moved
    # to the top of your test file if they are not already present.


def test_get_project_approved_trusts_project_not_approved(
    app_fixture: FastAPI,
    client: TestClient,
    mock_project: Projects,  # Assuming this fixture provides a Projects model instance or a compatible mock
):
    # Arrange
    mock_db_session = MagicMock()
    app_fixture.dependency_overrides[get_session] = lambda: mock_db_session
    app_fixture.dependency_overrides[verify_token] = lambda: TEST_USER_ID

    # Configure the mock_project to have a non-approved status
    mock_project.status = ProjectStatus.UNSTAGED  # Or any status other than APPROVED

    with (
        patch("flip_api.project_services.get_project_approved_trusts.can_access_project", return_value=True),
        patch("flip_api.project_services.get_project_approved_trusts.get_project", return_value=mock_project),
        patch(
            "flip_api.project_services.get_project_approved_trusts.get_approved_trusts_for_project"
        ) as mock_get_trusts,
    ):
        response = client.get(f"/api/projects/{TEST_PROJECT_ID}/trusts/approved")

    # Assert
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["detail"] == "Project has not been approved yet or does not exist."
    mock_get_trusts.assert_not_called()


def test_get_project_approved_trusts_value_error_fetching_trusts(
    app_fixture: FastAPI,
    client: TestClient,
    mock_project: Projects,
):
    # Arrange
    mock_db_session = MagicMock()
    app_fixture.dependency_overrides[get_session] = lambda: mock_db_session
    app_fixture.dependency_overrides[verify_token] = lambda: TEST_USER_ID

    mock_project.status = ProjectStatus.APPROVED

    with (
        patch("flip_api.project_services.get_project_approved_trusts.can_access_project", return_value=True),
        patch("flip_api.project_services.get_project_approved_trusts.get_project", return_value=mock_project),
        patch(
            "flip_api.project_services.get_project_approved_trusts.get_approved_trusts_for_project",
            side_effect=ValueError("Test Value Error"),
        ) as mock_get_trusts,
    ):
        response = client.get(f"/api/projects/{TEST_PROJECT_ID}/trusts/approved")

    # Assert
    assert response.status_code == status.HTTP_200_OK  # As per current implementation, returns 200 with empty list
    assert response.json() == []
    mock_get_trusts.assert_called_once_with(TEST_PROJECT_ID, mock_db_session)


def test_get_project_approved_trusts_generic_exception_fetching_trusts(
    app_fixture: FastAPI,
    client: TestClient,
    mock_project: Projects,
):
    # Arrange
    mock_db_session = MagicMock()
    app_fixture.dependency_overrides[get_session] = lambda: mock_db_session
    app_fixture.dependency_overrides[verify_token] = lambda: TEST_USER_ID

    mock_project.status = ProjectStatus.APPROVED

    with (
        patch("flip_api.project_services.get_project_approved_trusts.can_access_project", return_value=True),
        patch("flip_api.project_services.get_project_approved_trusts.get_project", return_value=mock_project),
        patch(
            "flip_api.project_services.get_project_approved_trusts.get_approved_trusts_for_project",
            side_effect=Exception("Test Generic Error"),
        ) as mock_get_trusts,
    ):
        response = client.get(f"/api/projects/{TEST_PROJECT_ID}/trusts/approved")

    # Assert
    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert response.json()["detail"] == "An unexpected error occurred while retrieving approved trusts."
    mock_get_trusts.assert_called_once_with(TEST_PROJECT_ID, mock_db_session)


def test_get_project_approved_trusts_success_with_data(
    app_fixture: FastAPI,
    client: TestClient,
    mock_project: Projects,
):
    # Arrange
    mock_db_session = MagicMock()
    app_fixture.dependency_overrides[get_session] = lambda: mock_db_session
    app_fixture.dependency_overrides[verify_token] = lambda: TEST_USER_ID

    mock_project.status = ProjectStatus.APPROVED
    expected_trusts_data = [
        Trust(id=uuid.uuid4(), name="Trust 1", endpoint="https://t1.com"),
        Trust(id=uuid.uuid4(), name="Trust 2", endpoint="https://t2.com"),
    ]
    # Convert Pydantic models to dicts for comparison with JSON response
    expected_trusts_json = [trust.model_dump(mode="json") for trust in expected_trusts_data]

    with (
        patch("flip_api.project_services.get_project_approved_trusts.can_access_project", return_value=True),
        patch("flip_api.project_services.get_project_approved_trusts.get_project", return_value=mock_project),
        patch(
            "flip_api.project_services.get_project_approved_trusts.get_approved_trusts_for_project",
            return_value=expected_trusts_data,
        ) as mock_get_trusts,
    ):
        response = client.get(f"/api/projects/{TEST_PROJECT_ID}/trusts/approved")

    # Assert
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == expected_trusts_json
    mock_get_trusts.assert_called_once_with(TEST_PROJECT_ID, mock_db_session)


def test_get_project_approved_trusts_invalid_project_id_format(
    app_fixture: FastAPI,
    client: TestClient,
):
    # Arrange
    # No specific mocks needed here as FastAPI handles path parameter validation.
    # Overrides are set for completeness if any part of the dependency chain is hit before validation.
    mock_db_session = MagicMock()
    app_fixture.dependency_overrides[get_session] = lambda: mock_db_session
    app_fixture.dependency_overrides[verify_token] = lambda: TEST_USER_ID

    invalid_project_id = "not-a-uuid"
    response = client.get(f"/api/projects/{invalid_project_id}/trusts/approved")

    # Assert
    # FastAPI's default for invalid UUID path parameters is 422 Unprocessable Entity
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    # Check for a more specific error detail if necessary, e.g., related to 'project_id'
    # Example: assert any(err["loc"] == ["path", "project_id"] for err in response.json()["detail"])
