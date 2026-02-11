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
from uuid import uuid4

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient

from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.db.models.main_models import Queries as DbQueries
from flip_api.domain.interfaces.project import IImagingStatus, IProjectQuery, IProjectResponse
from flip_api.domain.schemas.status import ProjectStatus
from flip_api.project_services.get_imaging_project_status import router as get_imaging_project_status_router

# Assuming Queries is the model for project_response.query


@pytest.fixture
def app_fixture() -> FastAPI:
    app = FastAPI()
    app.include_router(get_imaging_project_status_router)
    return app


@pytest.fixture
def client(app_fixture: FastAPI) -> TestClient:
    return TestClient(app_fixture)


# Mock data constants
MOCK_USER_ID = uuid4()
MOCK_PROJECT_ID = uuid4()
MOCK_QUERY_ID = uuid4()
MOCK_QUERY_STRING = "SELECT * FROM studies WHERE modality='MRI'"
MOCK_ENCODED_QUERY = "U0VMRUNUICogRlJPTSBzdHVkaWVzIFdIRVJFIG1vZGFsaXR5PSdNUkkn"  # base64 of MOCK_QUERY_STRING

# Mock for project_response.query
mock_project_query_obj = DbQueries(id=MOCK_QUERY_ID, query=MOCK_QUERY_STRING, project_id=MOCK_PROJECT_ID)

# Mock for the object returned by get_project
mock_project_response_obj = MagicMock()
mock_project_response_obj.query = mock_project_query_obj
mock_project_response_obj.id = MOCK_PROJECT_ID  # Ensure it has an id

mock_imaging_projects_list = [MagicMock(id="img_proj_1"), MagicMock(id="img_proj_2")]
mock_imaging_statuses_list_data = [
    IImagingStatus(
        trust_id=uuid4(),
        trust_name="Trust A",
        project_creation_completed=True,
    ).model_dump(mode="json", by_alias=True),  # type: ignore[call-arg]
    IImagingStatus(
        trust_id=uuid4(),
        trust_name="Trust B",
        project_creation_completed=False,
    ).model_dump(mode="json", by_alias=True),  # type: ignore[call-arg]
]


def test_get_imaging_project_status_success(client: TestClient, app_fixture: FastAPI):
    mock_db_session = MagicMock()
    app_fixture.dependency_overrides[get_session] = lambda: mock_db_session
    app_fixture.dependency_overrides[verify_token] = lambda: MOCK_USER_ID

    with (
        patch(
            "flip_api.project_services.get_imaging_project_status.can_access_project", return_value=True
        ) as mock_can_access,
        patch(
            "flip_api.project_services.get_imaging_project_status.get_project",
            return_value=mock_project_response_obj,
        ) as mock_get_project,
        patch(
            "flip_api.project_services.get_imaging_project_status.get_imaging_projects",
            return_value=mock_imaging_projects_list,
        ) as mock_get_imaging_projects,
        patch(
            "flip_api.project_services.get_imaging_project_status.base64_url_encode",
            return_value=MOCK_ENCODED_QUERY,
        ) as mock_base64_encode,
        patch(
            "flip_api.project_services.get_imaging_project_status.get_imaging_project_statuses",
            return_value=mock_imaging_statuses_list_data,
        ) as mock_get_statuses,
    ):
        response = client.get(f"/projects/{str(MOCK_PROJECT_ID)}/image/status")

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == mock_imaging_statuses_list_data

        mock_can_access.assert_called_once_with(MOCK_USER_ID, MOCK_PROJECT_ID, mock_db_session)
        mock_get_project.assert_called_once_with(MOCK_PROJECT_ID, mock_db_session)
        mock_get_imaging_projects.assert_called_once_with(MOCK_PROJECT_ID, mock_db_session)
        mock_base64_encode.assert_called_once_with(mock_project_query_obj.query)
        mock_get_statuses.assert_called_once_with(mock_imaging_projects_list, MOCK_ENCODED_QUERY, mock_db_session)

    app_fixture.dependency_overrides.clear()


def test_get_imaging_project_status_forbidden(client: TestClient, app_fixture: FastAPI):
    mock_db_session = MagicMock()
    app_fixture.dependency_overrides[get_session] = lambda: mock_db_session
    app_fixture.dependency_overrides[verify_token] = lambda: MOCK_USER_ID

    with (
        patch(
            "flip_api.project_services.get_imaging_project_status.can_access_project", return_value=False
        ) as mock_can_access,
        patch("flip_api.project_services.get_imaging_project_status.get_project") as mock_get_project,
    ):
        response = client.get(f"/projects/{str(MOCK_PROJECT_ID)}/image/status")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json()["detail"] == "You do not have permission to access this project."
        mock_can_access.assert_called_once_with(MOCK_USER_ID, MOCK_PROJECT_ID, mock_db_session)
        mock_get_project.assert_not_called()

    app_fixture.dependency_overrides.clear()


def test_get_imaging_project_status_project_not_found(
    client: TestClient,
    app_fixture: FastAPI,
    project_with_approved_trusts,
    project_id,
):
    mock_db_session = MagicMock()
    app_fixture.dependency_overrides[get_session] = lambda: mock_db_session
    app_fixture.dependency_overrides[verify_token] = lambda: MOCK_USER_ID

    with (
        patch(
            "flip_api.project_services.get_imaging_project_status.can_access_project", return_value=True
        ) as mock_can_access,
        patch(
            "flip_api.project_services.get_imaging_project_status.get_project", return_value=None
        ) as mock_get_project,
    ):
        mock_get_project.return_value = IProjectResponse(
            id=uuid4(),
            name="Test Project",
            query=IProjectQuery(
                id=uuid4(),
                name="Age Query",
                query="SELECT * FROM table WHERE age > 50",
                trusts_queried=5,
                total_cohort=100,
            ),  # type: ignore[call-arg]
            owner_id=MOCK_USER_ID,
            creation_timestamp=datetime.utcnow(),
            status=ProjectStatus.UNSTAGED,
            query_id=uuid4(),
        )
        response = client.get(f"/projects/{str(project_id)}/image/status")
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.json()["detail"] == "The imaging project was not found."
        mock_can_access.assert_called_once_with(MOCK_USER_ID, project_id, mock_db_session)
        mock_get_project.assert_called_once_with(project_id, mock_db_session)

    app_fixture.dependency_overrides.clear()


def test_get_imaging_project_status_project_query_not_found(client: TestClient, app_fixture: FastAPI):
    mock_db_session = MagicMock()
    app_fixture.dependency_overrides[get_session] = lambda: mock_db_session
    app_fixture.dependency_overrides[verify_token] = lambda: MOCK_USER_ID

    project_response_no_query = MagicMock()
    project_response_no_query.query = None  # Simulate project exists but has no query object

    with (
        patch(
            "flip_api.project_services.get_imaging_project_status.can_access_project", return_value=True
        ) as mock_can_access,
        patch(
            "flip_api.project_services.get_imaging_project_status.get_project",
            return_value=project_response_no_query,
        ) as mock_get_project,
    ):
        response = client.get(f"/projects/{str(MOCK_PROJECT_ID)}/image/status")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.json()["detail"] == "The project query was not found."
        mock_can_access.assert_called_once_with(MOCK_USER_ID, MOCK_PROJECT_ID, mock_db_session)
        mock_get_project.assert_called_once_with(MOCK_PROJECT_ID, mock_db_session)

    app_fixture.dependency_overrides.clear()


def test_get_imaging_project_status_imaging_projects_not_found(client: TestClient, app_fixture: FastAPI):
    mock_db_session = MagicMock()
    app_fixture.dependency_overrides[get_session] = lambda: mock_db_session
    app_fixture.dependency_overrides[verify_token] = lambda: MOCK_USER_ID

    with (
        patch("flip_api.project_services.get_imaging_project_status.can_access_project", return_value=True),
        patch(
            "flip_api.project_services.get_imaging_project_status.get_project",
            return_value=mock_project_response_obj,
        ),
        patch(
            "flip_api.project_services.get_imaging_project_status.get_imaging_projects", return_value=None
        ) as mock_get_imaging_projects,
    ):
        response = client.get(f"/projects/{str(MOCK_PROJECT_ID)}/image/status")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.json()["detail"] == "The imaging project was not found."
        mock_get_imaging_projects.assert_called_once_with(MOCK_PROJECT_ID, mock_db_session)

    app_fixture.dependency_overrides.clear()


def test_get_imaging_project_status_statuses_not_found(client: TestClient, app_fixture: FastAPI):
    mock_db_session = MagicMock()
    app_fixture.dependency_overrides[get_session] = lambda: mock_db_session
    app_fixture.dependency_overrides[verify_token] = lambda: MOCK_USER_ID

    with (
        patch("flip_api.project_services.get_imaging_project_status.can_access_project", return_value=True),
        patch(
            "flip_api.project_services.get_imaging_project_status.get_project",
            return_value=mock_project_response_obj,
        ),
        patch(
            "flip_api.project_services.get_imaging_project_status.get_imaging_projects",
            return_value=mock_imaging_projects_list,
        ),
        patch(
            "flip_api.project_services.get_imaging_project_status.base64_url_encode",
            return_value=MOCK_ENCODED_QUERY,
        ),
        patch(
            "flip_api.project_services.get_imaging_project_status.get_imaging_project_statuses", return_value=None
        ) as mock_get_statuses,
    ):
        response = client.get(f"/projects/{str(MOCK_PROJECT_ID)}/image/status")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.json()["detail"] == "The imaging project status was not found."
        mock_get_statuses.assert_called_once_with(mock_imaging_projects_list, MOCK_ENCODED_QUERY, mock_db_session)

    app_fixture.dependency_overrides.clear()


def test_get_imaging_project_status_invalid_project_id_format(client: TestClient, app_fixture: FastAPI):
    # No dependency overrides needed as FastAPI validation happens before endpoint logic
    project_id = "not-a-valid-uuid"
    response = client.get(f"/projects/{project_id}/image/status")

    assert response.status_code == status.HTTP_403_FORBIDDEN or response.status_code == status.HTTP_401_UNAUTHORIZED

    app_fixture.dependency_overrides.clear()
