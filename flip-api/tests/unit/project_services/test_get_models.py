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
from uuid import uuid4

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient

from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.domain.interfaces.project import (
    IModelsInfoResponse,
    ModelStatus,
)
from flip_api.project_services.get_models import router as get_models_router
from flip_api.utils.paging_utils import IPagedResponse


@pytest.fixture
def app_fixture() -> FastAPI:
    app = FastAPI()
    app.include_router(get_models_router, prefix="/api")
    return app


@pytest.fixture
def client(app_fixture: FastAPI) -> TestClient:
    return TestClient(app_fixture)


MOCK_USER_ID = uuid4()
MOCK_PROJECT_ID = uuid4()
MOCK_MODEL_ID_1 = uuid4()
MOCK_MODEL_ID_2 = uuid4()


# Expected response data, status should match the DB model's status
# as per current get_models.py logic: status=model.status
expected_model_1_response = IModelsInfoResponse(
    id=MOCK_MODEL_ID_1,
    name="Model 1",
    description="Description 1",
    status=ModelStatus.INITIATED,  # Corrected to match mock_model_1_db.status
    owner_id=MOCK_USER_ID,
)
expected_model_2_response = IModelsInfoResponse(
    id=MOCK_MODEL_ID_2,
    name="Model 2",
    description="Description 2",
    status=ModelStatus.PENDING,  # Corrected to match mock_model_2_db.status
    owner_id=MOCK_USER_ID,
)
expected_response_list = [
    expected_model_1_response.model_dump(mode="json"),
    expected_model_2_response.model_dump(mode="json"),
]
mock_db_models_list = [expected_model_1_response, expected_model_2_response]
mock_get_models_service_response: tuple[IPagedResponse, MagicMock] = (
    IPagedResponse(
        data=mock_db_models_list,
        total_rows=1,
    ),
    MagicMock(page_size=10),
)

mock_get_models_service_empty_response: tuple[IPagedResponse, MagicMock] = (
    IPagedResponse(
        data=[],
        total_rows=0,
    ),
    MagicMock(page_size=10),
)


def test_get_models_success(client: TestClient, app_fixture: FastAPI):
    mock_db_session = MagicMock()
    mock_project_instance = MagicMock(id=MOCK_PROJECT_ID)

    app_fixture.dependency_overrides[get_session] = lambda: mock_db_session
    app_fixture.dependency_overrides[verify_token] = lambda: MOCK_USER_ID

    with (
        patch("flip_api.project_services.get_models.can_access_project", return_value=True) as mock_can_access,
        patch(
            "flip_api.project_services.get_models.get_project", return_value=mock_project_instance
        ) as mock_get_project,
        patch(
            "flip_api.project_services.get_models.get_project_models_service",
            return_value=mock_get_models_service_response,
        ) as mock_get_project_models,
    ):
        response = client.get(f"/api/projects/{str(MOCK_PROJECT_ID)}/models")

    assert response.status_code == status.HTTP_200_OK
    # Ensure response JSON matches the corrected expected list
    assert response.json()["data"] == expected_response_list

    mock_can_access.assert_called_once_with(MOCK_USER_ID, MOCK_PROJECT_ID, mock_db_session)
    mock_get_project.assert_called_once_with(MOCK_PROJECT_ID, mock_db_session)
    mock_get_project_models.assert_called_once_with(MOCK_PROJECT_ID, mock_db_session, {})

    app_fixture.dependency_overrides.clear()


def test_get_models_access_denied(client: TestClient, app_fixture: FastAPI):
    mock_db_session = MagicMock()
    app_fixture.dependency_overrides[get_session] = lambda: mock_db_session
    app_fixture.dependency_overrides[verify_token] = lambda: MOCK_USER_ID

    with (
        patch("flip_api.project_services.get_models.can_access_project", return_value=False) as mock_can_access,
        patch("flip_api.project_services.get_models.get_project") as mock_get_project,
        patch("flip_api.project_services.get_models.get_project_models_service") as mock_get_project_models,
    ):
        response = client.get(f"/api/projects/{str(MOCK_PROJECT_ID)}/models")

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["detail"] == f"User with ID: {MOCK_USER_ID} is denied access to this project"
    mock_can_access.assert_called_once_with(MOCK_USER_ID, MOCK_PROJECT_ID, mock_db_session)
    mock_get_project.assert_not_called()
    mock_get_project_models.assert_not_called()

    app_fixture.dependency_overrides.clear()


def test_get_models_project_not_found(client: TestClient, app_fixture: FastAPI):
    mock_db_session = MagicMock()
    app_fixture.dependency_overrides[get_session] = lambda: mock_db_session
    app_fixture.dependency_overrides[verify_token] = lambda: MOCK_USER_ID

    with (
        patch("flip_api.project_services.get_models.can_access_project", return_value=True) as mock_can_access,
        patch("flip_api.project_services.get_models.get_project", return_value=None) as mock_get_project,
        patch("flip_api.project_services.get_models.get_project_models_service") as mock_get_project_models,
    ):
        response = client.get(f"/api/projects/{str(MOCK_PROJECT_ID)}/models")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["detail"] == f"Project with ID: {MOCK_PROJECT_ID} not found."
    mock_can_access.assert_called_once_with(MOCK_USER_ID, MOCK_PROJECT_ID, mock_db_session)
    mock_get_project.assert_called_once_with(MOCK_PROJECT_ID, mock_db_session)
    mock_get_project_models.assert_not_called()

    app_fixture.dependency_overrides.clear()


def test_get_models_no_models_found_for_project(client: TestClient, app_fixture: FastAPI):
    mock_db_session = MagicMock()
    mock_project_instance = MagicMock(id=MOCK_PROJECT_ID)
    app_fixture.dependency_overrides[get_session] = lambda: mock_db_session
    app_fixture.dependency_overrides[verify_token] = lambda: MOCK_USER_ID

    with (
        patch("flip_api.project_services.get_models.can_access_project", return_value=True) as mock_can_access,
        patch(
            "flip_api.project_services.get_models.get_project", return_value=mock_project_instance
        ) as mock_get_project,
        patch(
            "flip_api.project_services.get_models.get_project_models_service",
            return_value=mock_get_models_service_empty_response,
        ) as mock_get_project_models,
    ):
        response = client.get(f"/api/projects/{str(MOCK_PROJECT_ID)}/models")

    assert response.status_code == status.HTTP_200_OK
    mock_can_access.assert_called_once_with(MOCK_USER_ID, MOCK_PROJECT_ID, mock_db_session)
    mock_get_project.assert_called_once_with(MOCK_PROJECT_ID, mock_db_session)
    mock_get_project_models.assert_called_once_with(MOCK_PROJECT_ID, mock_db_session, {})

    app_fixture.dependency_overrides.clear()
