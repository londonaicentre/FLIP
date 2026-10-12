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

from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient

from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.db.models.user_models import PermissionRef
from flip_api.domain.interfaces.project import ProjectStatus
from flip_api.project_services.unstage_project import router as unstage_project_router


@pytest.fixture
def app_fixture() -> FastAPI:
    app = FastAPI()
    app.include_router(unstage_project_router, prefix="/api")
    return app


@pytest.fixture
def client(app_fixture: FastAPI) -> TestClient:
    return TestClient(app_fixture)


@pytest.fixture
def test_user_id():
    return UUID("12345678-1234-5678-9012-123456789012")


@pytest.fixture
def test_project_id():
    return UUID("87654321-4321-8765-2109-876543210987")


@pytest.fixture
def mock_project_data():
    mock_project = MagicMock()
    mock_project.status = ProjectStatus.STAGED.value
    return mock_project


def test_unstage_project_success(app_fixture, client, test_user_id, test_project_id, mock_project_data):
    # Arrange
    mock_session = MagicMock()
    app_fixture.dependency_overrides[get_session] = lambda: mock_session
    app_fixture.dependency_overrides[verify_token] = lambda: test_user_id

    with (
        patch("flip_api.project_services.unstage_project.has_permissions", return_value=True),
        patch("flip_api.project_services.unstage_project.get_project", return_value=mock_project_data),
        patch("flip_api.project_services.unstage_project.unstage_project_service") as mock_unstage_service,
    ):
        # Act
        response = client.post(f"/api/projects/{test_project_id}/unstage")

    # Assert
    assert response.status_code == status.HTTP_204_NO_CONTENT
    mock_unstage_service.assert_called_once_with(
        project_id=test_project_id, current_user_id=test_user_id, session=mock_session
    )


def test_unstage_project_permission_denied(app_fixture, client, test_user_id, test_project_id):
    # Arrange
    mock_session = MagicMock()
    app_fixture.dependency_overrides[get_session] = lambda: mock_session
    app_fixture.dependency_overrides[verify_token] = lambda: test_user_id

    with (
        patch("flip_api.project_services.unstage_project.has_permissions", return_value=False),
        patch("flip_api.project_services.unstage_project.get_project") as mock_get_project,
    ):
        # Act
        response = client.post(f"/api/projects/{test_project_id}/unstage")

    # Assert
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["detail"] == f"User with ID: {test_user_id} is not allowed to unstage projects"
    mock_get_project.assert_not_called()


def test_unstage_project_not_found(app_fixture, client, test_user_id, test_project_id):
    # Arrange
    mock_session = MagicMock()
    app_fixture.dependency_overrides[get_session] = lambda: mock_session
    app_fixture.dependency_overrides[verify_token] = lambda: test_user_id

    with (
        patch("flip_api.project_services.unstage_project.has_permissions", return_value=True),
        patch("flip_api.project_services.unstage_project.get_project", return_value=None),
    ):
        # Act
        response = client.post(f"/api/projects/{test_project_id}/unstage")

    # Assert
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["detail"] == f"Unable to find project with ID: {test_project_id}"


def test_unstage_project_not_staged_status(app_fixture, client, test_user_id, test_project_id):
    # Arrange
    mock_session = MagicMock()
    app_fixture.dependency_overrides[get_session] = lambda: mock_session
    app_fixture.dependency_overrides[verify_token] = lambda: test_user_id

    mock_project_data = MagicMock()
    mock_project_data.status = ProjectStatus.UNSTAGED.value

    with (
        patch("flip_api.project_services.unstage_project.has_permissions", return_value=True),
        patch("flip_api.project_services.unstage_project.get_project", return_value=mock_project_data),
    ):
        # Act
        response = client.post(f"/api/projects/{test_project_id}/unstage")

    # Assert
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["detail"] == f"Project with ID: {test_project_id} is not currently staged."


def test_unstage_project_approved_status(app_fixture, client, test_user_id, test_project_id):
    # Arrange
    mock_session = MagicMock()
    app_fixture.dependency_overrides[get_session] = lambda: mock_session
    app_fixture.dependency_overrides[verify_token] = lambda: test_user_id

    mock_project_data = MagicMock()
    mock_project_data.status = ProjectStatus.APPROVED.value

    with (
        patch("flip_api.project_services.unstage_project.has_permissions", return_value=True),
        patch("flip_api.project_services.unstage_project.get_project", return_value=mock_project_data),
    ):
        # Act
        response = client.post(f"/api/projects/{test_project_id}/unstage")

    # Assert
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["detail"] == f"Project with ID: {test_project_id} is not currently staged."


def test_unstage_project_value_error_from_service(
    app_fixture, client, test_user_id, test_project_id, mock_project_data
):
    # Arrange
    mock_session = MagicMock()
    app_fixture.dependency_overrides[get_session] = lambda: mock_session
    app_fixture.dependency_overrides[verify_token] = lambda: test_user_id

    with (
        patch("flip_api.project_services.unstage_project.has_permissions", return_value=True),
        patch("flip_api.project_services.unstage_project.get_project", return_value=mock_project_data),
        patch(
            "flip_api.project_services.unstage_project.unstage_project_service",
            side_effect=ValueError("Invalid project state for unstaging"),
        ),
    ):
        # Act
        response = client.post(f"/api/projects/{test_project_id}/unstage")

    # Assert
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["detail"] == "Invalid project state for unstaging"


def test_unstage_project_generic_exception_from_service(
    app_fixture, client, test_user_id, test_project_id, mock_project_data
):
    # Arrange
    mock_session = MagicMock()
    app_fixture.dependency_overrides[get_session] = lambda: mock_session
    app_fixture.dependency_overrides[verify_token] = lambda: test_user_id

    with (
        patch("flip_api.project_services.unstage_project.has_permissions", return_value=True),
        patch("flip_api.project_services.unstage_project.get_project", return_value=mock_project_data),
        patch(
            "flip_api.project_services.unstage_project.unstage_project_service",
            side_effect=Exception("Database connection error"),
        ),
    ):
        # Act
        response = client.post(f"/api/projects/{test_project_id}/unstage")

    # Assert
    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert response.json()["detail"] == "An unexpected error occurred while unstaging the project."


def test_unstage_project_invalid_project_id_format(app_fixture, client, test_user_id):
    # Arrange
    mock_session = MagicMock()
    app_fixture.dependency_overrides[get_session] = lambda: mock_session
    app_fixture.dependency_overrides[verify_token] = lambda: test_user_id

    invalid_project_id = "not-a-valid-uuid"

    # Act
    response = client.post(f"/api/projects/{invalid_project_id}/unstage")

    # Assert
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    # FastAPI automatically validates UUID path parameters and returns 422 for invalid formats


def test_unstage_project_permission_check_called_correctly(app_fixture, client, test_user_id, test_project_id):
    # Arrange
    mock_session = MagicMock()
    app_fixture.dependency_overrides[get_session] = lambda: mock_session
    app_fixture.dependency_overrides[verify_token] = lambda: test_user_id

    with (
        patch("flip_api.project_services.unstage_project.has_permissions", return_value=True) as mock_has_permissions,
        patch("flip_api.project_services.unstage_project.get_project", return_value=None),
    ):
        # Act
        client.post(f"/api/projects/{test_project_id}/unstage")

    # Assert
    mock_has_permissions.assert_called_once_with(test_user_id, [PermissionRef.CAN_UNSTAGE_PROJECTS], mock_session)


def test_unstage_project_session_transaction_management(
    app_fixture, client, test_user_id, test_project_id, mock_project_data
):
    # Arrange
    mock_session = MagicMock()
    app_fixture.dependency_overrides[get_session] = lambda: mock_session
    app_fixture.dependency_overrides[verify_token] = lambda: test_user_id

    with (
        patch("flip_api.project_services.unstage_project.has_permissions", return_value=True),
        patch("flip_api.project_services.unstage_project.get_project", return_value=mock_project_data),
        patch("flip_api.project_services.unstage_project.unstage_project_service") as mock_unstage_service,
    ):
        # Act
        response = client.post(f"/api/projects/{test_project_id}/unstage")

    # Assert
    assert response.status_code == status.HTTP_204_NO_CONTENT
    mock_unstage_service.assert_called_once_with(
        project_id=test_project_id, current_user_id=test_user_id, session=mock_session
    )


def test_unstage_project_edge_case_empty_uuid(app_fixture, client, test_user_id):
    # Arrange
    mock_session = MagicMock()
    app_fixture.dependency_overrides[get_session] = lambda: mock_session
    app_fixture.dependency_overrides[verify_token] = lambda: test_user_id

    # UUID with all zeros should still be valid UUID format
    zero_uuid = "00000000-0000-0000-0000-000000000000"

    with (
        patch("flip_api.project_services.unstage_project.has_permissions", return_value=True),
        patch("flip_api.project_services.unstage_project.get_project", return_value=None),
    ):
        # Act
        response = client.post(f"/api/projects/{zero_uuid}/unstage")

    # Assert
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["detail"] == f"Unable to find project with ID: {zero_uuid}"
