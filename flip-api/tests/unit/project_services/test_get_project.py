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

from datetime import datetime
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.domain.schemas.status import ProjectStatus
from flip_api.domain.schemas.users import CognitoUser
from flip_api.main import app

# Test constants
TEST_PROJECT_ID = UUID("11111111-1111-1111-1111-111111111111")
TEST_USER_ID = UUID("22222222-2222-2222-2222-222222222222")
TEST_OWNER_ID = UUID("33333333-3333-3333-3333-333333333333")
TEST_QUERY_ID = UUID("44444444-4444-4444-4444-444444444444")


# ---- Test fixtures ----
@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.fixture(autouse=True)
def override_dependencies(mock_db):
    # Override the DB session
    app.dependency_overrides[get_session] = lambda: mock_db
    app.dependency_overrides[verify_token] = lambda: TEST_USER_ID

    yield

    app.dependency_overrides.clear()


@pytest.fixture
def mock_project():
    """Mock project object returned from get_project"""
    project = MagicMock()
    project.id = TEST_PROJECT_ID
    project.name = "Test Project"
    project.owner_id = TEST_OWNER_ID
    project.query_id = TEST_QUERY_ID
    project.status = ProjectStatus.STAGED
    project.creation_timestamp = "2023-01-01T00:00:00"
    return project


@pytest.fixture
def mock_owner():
    """Mock user object returned from get_user_by_email_or_id"""
    owner = MagicMock()
    owner.email = "owner@example.com"
    return owner


# @pytest.fixture
# def mock_approved_trusts():
#     """Mock approved trusts returned from get_approved_trusts_for_project"""
#     return [
#         {"id": "trust1", "name": "Trust 1", "status": "APPROVED"},
#         {"id": "trust2", "name": "Trust 2", "status": "PENDING"},
#     ]


# @pytest.fixture
# def mock_users_with_access():
#     """Mock users with access returned from get_users_with_access"""
#     return [
#         {"id": str(TEST_USER_ID), "email": "user@example.com", "name": "Test User"},
#         {"id": str(TEST_OWNER_ID), "email": "owner@example.com", "name": "Owner User"},
#     ]


def test_get_project_details_no_access(client, mock_db):
    """Test project details access denied"""
    # Arrange
    with patch("flip_api.project_services.get_project.can_access_project", return_value=False):
        # Act
        response = client.get(f"/api/projects/{TEST_PROJECT_ID}")

        # Assert
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json()["detail"] == "User does not have access to this project."


def test_get_project_details_not_found(client, mock_db):
    """Test project not found"""
    # Arrange
    with (
        patch("flip_api.project_services.get_project.can_access_project", return_value=True),
        patch("flip_api.project_services.get_project.get_project", return_value=None),
    ):
        # Act
        response = client.get(f"/api/projects/{TEST_PROJECT_ID}")

        # Assert
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.json()["detail"] == f"Project with ID: {TEST_PROJECT_ID} not found."


def test_get_project_details_invalid_id(client, mock_db):
    """Test invalid project ID"""
    # Act
    response = client.get("/api/projects/not-a-uuid")

    # Assert - FastAPI should return 422 for invalid UUID
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_get_project_details_returns_users_as_objects(client, mock_db, mock_project):
    """Test that users are returned as objects with id, email, and isDisabled — and nothing more."""
    owner_user = CognitoUser(id=TEST_OWNER_ID, email="owner@example.com", is_disabled=False)
    access_user = CognitoUser(id=TEST_USER_ID, email="user@example.com", is_disabled=False)

    mock_project.description = "Test description"
    mock_project.creation_timestamp = datetime(2023, 1, 1)

    with (
        patch("flip_api.project_services.get_project.can_access_project", return_value=True),
        patch("flip_api.project_services.get_project.get_project", return_value=mock_project),
        patch("flip_api.project_services.get_project.get_trusts_approval_status_for_project", return_value=[]),
        patch("flip_api.project_services.get_project.get_users_with_access", return_value=[TEST_USER_ID]),
        patch("flip_api.project_services.get_project.get_project_query", return_value=None),
        patch("flip_api.project_services.get_project.get_settings") as mock_settings,
        patch("flip_api.project_services.get_project.get_user_by_email_or_id") as mock_get_user,
    ):
        mock_settings.return_value.AWS_COGNITO_USER_POOL_ID = "test-pool"
        # First call: owner lookup (by user_id=TEST_OWNER_ID), second call: access user lookup
        mock_get_user.side_effect = [owner_user, access_user]

        response = client.get(f"/api/projects/{TEST_PROJECT_ID}")

    assert response.status_code == status.HTTP_200_OK
    data = response.json()

    # Users should be objects with id, email, isDisabled — not plain email strings
    assert isinstance(data["users"], list)
    assert len(data["users"]) == 2
    user_emails = {u["email"] for u in data["users"]}
    assert user_emails == {"owner@example.com", "user@example.com"}
    for user in data["users"]:
        assert "id" in user
        assert "email" in user
        assert "isDisabled" in user
        assert user["isDisabled"] is False
        # A project co-member must not learn anyone's name or organisation — that is the
        # disclosure FLIP#907 closed on `/users/{user_id}`, and this path is currently narrow only
        # because it never calls `apply_user_profile` (see the comment on IReturnedProject.users).
        # The keys are still on the wire because CognitoUser defaults them to "", so assert they
        # are empty rather than absent; enriching this path would populate them and fail here.
        # FLIP#996 tracks making the response model enforce it instead.
        assert user.get("name", "") == ""
        assert user.get("organisation", "") == ""


def test_get_project_details_filters_disabled_users(client, mock_db, mock_project):
    """Test that disabled users are excluded from the users list."""
    owner_user = CognitoUser(id=TEST_OWNER_ID, email="owner@example.com", is_disabled=False)
    disabled_user = CognitoUser(id=TEST_USER_ID, email="disabled@example.com", is_disabled=True)

    mock_project.description = "Test description"
    mock_project.creation_timestamp = datetime(2023, 1, 1)

    with (
        patch("flip_api.project_services.get_project.can_access_project", return_value=True),
        patch("flip_api.project_services.get_project.get_project", return_value=mock_project),
        patch("flip_api.project_services.get_project.get_trusts_approval_status_for_project", return_value=[]),
        patch("flip_api.project_services.get_project.get_users_with_access", return_value=[TEST_USER_ID]),
        patch("flip_api.project_services.get_project.get_project_query", return_value=None),
        patch("flip_api.project_services.get_project.get_settings") as mock_settings,
        patch("flip_api.project_services.get_project.get_user_by_email_or_id") as mock_get_user,
    ):
        mock_settings.return_value.AWS_COGNITO_USER_POOL_ID = "test-pool"
        mock_get_user.side_effect = [owner_user, disabled_user]

        response = client.get(f"/api/projects/{TEST_PROJECT_ID}")

    assert response.status_code == status.HTTP_200_OK
    data = response.json()

    # Only the non-disabled owner should be in the list
    assert len(data["users"]) == 1
    assert data["users"][0]["email"] == "owner@example.com"
