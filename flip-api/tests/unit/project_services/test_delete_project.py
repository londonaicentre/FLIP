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

from unittest.mock import ANY, MagicMock
from uuid import uuid4

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.main import app
from flip_api.project_services import delete_project as delete_project_module

# Mount the router once
app.include_router(delete_project_module.router)

client = TestClient(app)


@pytest.fixture(autouse=True)
def override_dependencies():
    # Create test values and mocks
    user_id = uuid4()
    mock_db = MagicMock()

    app.dependency_overrides[verify_token] = lambda: user_id
    app.dependency_overrides[get_session] = lambda: mock_db

    yield {
        "user_id": user_id,
        "mock_db": mock_db,
    }

    # Cleanup overrides
    app.dependency_overrides.clear()


@pytest.fixture
def mock_services(monkeypatch):
    mocks = {
        "can_access_project": MagicMock(return_value=True),
        "get_imaging_projects": MagicMock(return_value=[]),
        "delete_imaging_project": MagicMock(),
        "get_project_models_service": MagicMock(return_value=(MagicMock(data=[]), None)),
        "abort_model_training": MagicMock(),
        "delete_project": MagicMock(),
    }

    monkeypatch.setattr(delete_project_module, "can_access_project", mocks["can_access_project"])
    monkeypatch.setattr(delete_project_module, "get_imaging_projects", mocks["get_imaging_projects"])
    monkeypatch.setattr(delete_project_module, "delete_imaging_project", mocks["delete_imaging_project"])
    monkeypatch.setattr(delete_project_module, "get_project_models_service", mocks["get_project_models_service"])
    monkeypatch.setattr(delete_project_module, "abort_model_training", mocks["abort_model_training"])
    monkeypatch.setattr(delete_project_module, "delete_project", mocks["delete_project"])

    return mocks


def test_delete_project_success(override_dependencies, mock_services):
    project_id = uuid4()

    response = client.delete(f"/api/projects/{project_id}")

    assert response.status_code == status.HTTP_204_NO_CONTENT
    mock_services["delete_project"].assert_called_once()


def test_delete_project_forbidden(override_dependencies, mock_services):
    mock_services["can_access_project"].return_value = False
    project_id = uuid4()

    response = client.delete(f"/api/projects/{project_id}")

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert "denied access" in response.json()["detail"]


def test_delete_project_abort_training_called(override_dependencies, mock_services):
    model_mock = MagicMock()
    model_mock.id = uuid4()
    mock_services["get_project_models_service"].return_value = (MagicMock(data=[model_mock]), None)

    project_id = uuid4()
    response = client.delete(f"/api/projects/{project_id}")

    assert response.status_code == status.HTTP_204_NO_CONTENT
    mock_services["abort_model_training"].assert_called_once_with(
        request=ANY, model_id=model_mock.id, session=override_dependencies["mock_db"]
    )
