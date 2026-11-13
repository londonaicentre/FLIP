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
from flip_api.domain.interfaces.project import ProjectStatus
from flip_api.domain.schemas.projects import StageProjectRequest
from flip_api.project_services.stage_project import router as stage_project_router


@pytest.fixture
def client(test_user_id) -> TestClient:
    app = FastAPI()
    app.include_router(stage_project_router, prefix="/api")
    session = MagicMock()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[verify_token] = lambda: test_user_id
    return TestClient(app)


@pytest.fixture
def test_user_id():
    return UUID("12345678-1234-5678-9012-123456789012")


@pytest.fixture
def test_project_id():
    return UUID("87654321-4321-8765-2109-876543210987")


_TRUST_A = UUID("11111111-1111-1111-1111-111111111111")
_TRUST_B = UUID("22222222-2222-2222-2222-222222222222")
_TRUST_NEW = UUID("33333333-3333-3333-3333-333333333333")


@pytest.fixture
def stage_request_payload():
    stage_request = StageProjectRequest(trusts=[_TRUST_A, _TRUST_B])
    return stage_request.model_dump(mode="json")


@pytest.fixture
def mock_project_data():
    mock_project = MagicMock()
    mock_project.status = ProjectStatus.UNSTAGED.value
    mock_project.query = MagicMock()
    # Default: both trusts in the standard payload have responded successfully
    # with a usable (non-empty) cohort.
    mock_project.query.queried_trust_ids = [_TRUST_A, _TRUST_B]
    mock_project.query.responded_trust_ids = [_TRUST_A, _TRUST_B]
    mock_project.query.errored_trust_ids = []
    mock_project.query.empty_trust_ids = []
    return mock_project


def test_stage_project_success(client, test_project_id, stage_request_payload, mock_project_data):
    # Arrange
    with (
        patch("flip_api.project_services.stage_project.can_modify_project", return_value=True),
        patch("flip_api.project_services.stage_project.get_project", return_value=mock_project_data),
        patch("flip_api.project_services.stage_project.stage_project_service"),
    ):
        # Act
        response = client.post(f"/api/projects/{test_project_id}/stage", json=stage_request_payload)

    # Assert
    assert response.status_code == status.HTTP_204_NO_CONTENT


def test_stage_project_access_denied(client, test_user_id, test_project_id, stage_request_payload):
    # Arrange
    with (
        patch("flip_api.project_services.stage_project.can_modify_project", return_value=False),
        patch("flip_api.project_services.stage_project.get_project") as mock_get_project,
    ):
        # Act
        response = client.post(f"/api/projects/{test_project_id}/stage", json=stage_request_payload)

    # Assert
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["detail"] == f"User with ID: {test_user_id} is not allowed to stage this project."
    mock_get_project.assert_not_called()


def test_stage_project_not_found(client, test_project_id, stage_request_payload):
    # Arrange
    with (
        patch("flip_api.project_services.stage_project.can_modify_project", return_value=True),
        patch("flip_api.project_services.stage_project.get_project", return_value=None),
    ):
        # Act
        response = client.post(f"/api/projects/{test_project_id}/stage", json=stage_request_payload)

    # Assert
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["detail"] == f"Unable to find project with ID: {test_project_id}"


def test_stage_project_not_unstaged_status(client, test_project_id, stage_request_payload):
    # Arrange
    mock_project_data = MagicMock()
    mock_project_data.status = ProjectStatus.STAGED

    with (
        patch("flip_api.project_services.stage_project.can_modify_project", return_value=True),
        patch("flip_api.project_services.stage_project.get_project", return_value=mock_project_data),
    ):
        # Act
        response = client.post(f"/api/projects/{test_project_id}/stage", json=stage_request_payload)

    # Assert
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    # ProjectStatus is a StrEnum, so interpolating ProjectStatus.UNSTAGED into the error message renders the
    # bare value "UNSTAGED", not "ProjectStatus.UNSTAGED" as the old (str, Enum) did. Expected, not a regression.
    assert response.json()["detail"] == f"Project with ID: {test_project_id} is not 'UNSTAGED' and cannot be staged."


@pytest.mark.parametrize("query", [None, MagicMock(queried_trust_ids=[])], ids=["no-query", "no-trust-queried"])
def test_stage_project_invalid_cohort_query(client, test_project_id, stage_request_payload, mock_project_data, query):
    """A project with no cohort query, or a query with no trust responses (empty
    queried_trust_ids), cannot be staged — there's nothing to commit to."""
    # Arrange
    mock_project_data.query = query

    with (
        patch("flip_api.project_services.stage_project.can_modify_project", return_value=True),
        patch("flip_api.project_services.stage_project.get_project", return_value=mock_project_data),
    ):
        # Act
        response = client.post(f"/api/projects/{test_project_id}/stage", json=stage_request_payload)

    # Assert
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    expected_detail = (
        f"Project with ID: {test_project_id} does not have a valid cohort query with trusts queried and"
        " cannot be staged."
    )
    assert response.json()["detail"] == expected_detail


def test_stage_project_rejects_trust_not_in_cohort_query(client, test_project_id):
    """A trust that joined the platform after the cohort query was submitted
    has no QueryResult row, so its UUID is missing from queried_trust_ids.
    Staging against it must 400 — staging without a cohort count for that
    trust would mean the user is committing to data they've never seen."""
    mock_project_data = MagicMock()
    mock_project_data.status = ProjectStatus.UNSTAGED.value
    mock_project_data.query = MagicMock()
    mock_project_data.query.queried_trust_ids = [_TRUST_A, _TRUST_B]
    mock_project_data.query.responded_trust_ids = [_TRUST_A, _TRUST_B]
    mock_project_data.query.errored_trust_ids = []

    payload = StageProjectRequest(trusts=[_TRUST_A, _TRUST_NEW]).model_dump(mode="json")

    with (
        patch("flip_api.project_services.stage_project.can_modify_project", return_value=True),
        patch("flip_api.project_services.stage_project.get_project", return_value=mock_project_data),
        patch("flip_api.project_services.stage_project.stage_project_service") as mock_stage,
    ):
        response = client.post(f"/api/projects/{test_project_id}/stage", json=payload)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert str(_TRUST_NEW) in response.json()["detail"]
    mock_stage.assert_not_called()


def test_stage_project_rejects_errored_trust(client, test_project_id):
    """A trust that responded with an error has a QueryResult row but no
    usable count. Staging against it must 400 — otherwise the project commits
    to data we never received."""
    mock_project_data = MagicMock()
    mock_project_data.status = ProjectStatus.UNSTAGED.value
    mock_project_data.query = MagicMock()
    mock_project_data.query.queried_trust_ids = [_TRUST_A, _TRUST_B]
    mock_project_data.query.responded_trust_ids = [_TRUST_A, _TRUST_B]
    mock_project_data.query.errored_trust_ids = [_TRUST_B]  # errored

    payload = StageProjectRequest(trusts=[_TRUST_A, _TRUST_B]).model_dump(mode="json")

    with (
        patch("flip_api.project_services.stage_project.can_modify_project", return_value=True),
        patch("flip_api.project_services.stage_project.get_project", return_value=mock_project_data),
        patch("flip_api.project_services.stage_project.stage_project_service") as mock_stage,
    ):
        response = client.post(f"/api/projects/{test_project_id}/stage", json=payload)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert str(_TRUST_B) in response.json()["detail"]
    assert "error" in response.json()["detail"].lower()
    mock_stage.assert_not_called()


def test_stage_project_rejects_empty_cohort_trust(client, test_project_id):
    """A trust that responded with 0 records — a genuine zero match or a
    privacy-suppressed below-threshold count (#519) — has no cohort to build an
    imaging project against. Staging against it must 400."""
    mock_project_data = MagicMock()
    mock_project_data.status = ProjectStatus.UNSTAGED.value
    mock_project_data.query = MagicMock()
    mock_project_data.query.queried_trust_ids = [_TRUST_A, _TRUST_B]
    mock_project_data.query.responded_trust_ids = [_TRUST_A, _TRUST_B]
    mock_project_data.query.errored_trust_ids = []
    mock_project_data.query.empty_trust_ids = [_TRUST_B]  # 0 records / suppressed

    payload = StageProjectRequest(trusts=[_TRUST_A, _TRUST_B]).model_dump(mode="json")

    with (
        patch("flip_api.project_services.stage_project.can_modify_project", return_value=True),
        patch("flip_api.project_services.stage_project.get_project", return_value=mock_project_data),
        patch("flip_api.project_services.stage_project.stage_project_service") as mock_stage,
    ):
        response = client.post(f"/api/projects/{test_project_id}/stage", json=payload)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert str(_TRUST_B) in response.json()["detail"]
    assert "no cohort records" in response.json()["detail"].lower()
    mock_stage.assert_not_called()


def test_stage_project_rejects_never_responded_trust(client, test_project_id):
    """A trust that was dispatched but never posted a QueryResult has no
    cohort count. Staging against it must 400 — otherwise the project
    commits to data we never received."""
    mock_project_data = MagicMock()
    mock_project_data.status = ProjectStatus.UNSTAGED.value
    mock_project_data.query = MagicMock()
    # Both trusts dispatched; only A responded.
    mock_project_data.query.queried_trust_ids = [_TRUST_A, _TRUST_B]
    mock_project_data.query.responded_trust_ids = [_TRUST_A]
    mock_project_data.query.errored_trust_ids = []

    payload = StageProjectRequest(trusts=[_TRUST_A, _TRUST_B]).model_dump(mode="json")

    with (
        patch("flip_api.project_services.stage_project.can_modify_project", return_value=True),
        patch("flip_api.project_services.stage_project.get_project", return_value=mock_project_data),
        patch("flip_api.project_services.stage_project.stage_project_service") as mock_stage,
    ):
        response = client.post(f"/api/projects/{test_project_id}/stage", json=payload)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert str(_TRUST_B) in response.json()["detail"]
    assert "have not returned cohort query results" in response.json()["detail"]
    mock_stage.assert_not_called()


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_detail"),
    [
        (ValueError("Invalid trust configuration"), status.HTTP_400_BAD_REQUEST, "Invalid trust configuration"),
        # The raw exception text must never reach the caller on the 500 path.
        (
            Exception("Database error"),
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "An unexpected error occurred while staging the project.",
        ),
    ],
    ids=["value-error", "unexpected"],
)
def test_stage_project_service_error(
    client, test_project_id, stage_request_payload, mock_project_data, error, expected_status, expected_detail
):
    # Arrange
    with (
        patch("flip_api.project_services.stage_project.can_modify_project", return_value=True),
        patch("flip_api.project_services.stage_project.get_project", return_value=mock_project_data),
        patch("flip_api.project_services.stage_project.stage_project_service", side_effect=error),
    ):
        # Act
        response = client.post(f"/api/projects/{test_project_id}/stage", json=stage_request_payload)

    # Assert
    assert response.status_code == expected_status
    assert response.json()["detail"] == expected_detail


def test_stage_project_invalid_project_id_format(client, stage_request_payload):
    # Arrange
    invalid_project_id = "not-a-valid-uuid"

    # Act
    response = client.post(f"/api/projects/{invalid_project_id}/stage", json=stage_request_payload)

    # Assert
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    # FastAPI automatically validates UUID path parameters and returns 422 for invalid formats


def test_stage_project_missing_trusts_in_payload(client, test_project_id):
    # Arrange
    invalid_payload = {}  # Missing trusts field

    # Act
    response = client.post(f"/api/projects/{test_project_id}/stage", json=invalid_payload)

    # Assert
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    # FastAPI automatically validates request body and returns 422 for missing required fields
