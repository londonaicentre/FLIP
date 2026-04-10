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
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.db.models.main_models import Model
from flip_api.domain.schemas.status import ProjectStatus
from flip_api.main import app

client = TestClient(app)

test_user_id = "user-123"
test_project_id = uuid4()
test_model_id = uuid4()

test_payload = {
    "name": "My Model",
    "description": "A model to test",
    "projectId": str(test_project_id),
}

# ---------- Dependency Overrides ----------


@pytest.fixture(autouse=True)
def override_dependencies():
    mock_session = MagicMock()
    app.dependency_overrides[get_session] = lambda: mock_session
    app.dependency_overrides[verify_token] = lambda: test_user_id
    yield mock_session
    app.dependency_overrides.clear()


# ---------- Patch Fixtures ----------


@pytest.fixture
def mock_can_access_project_true():
    with patch("flip_api.model_services.save_model.can_access_project", return_value=True):
        yield


@pytest.fixture
def mock_can_access_project_false():
    with patch("flip_api.model_services.save_model.can_access_project", return_value=False):
        yield


@pytest.fixture
def mock_get_project_approved():
    mock_project = MagicMock()
    mock_project.status = ProjectStatus.APPROVED
    with patch("flip_api.model_services.save_model.get_project_by_id", return_value=mock_project):
        yield


@pytest.fixture
def mock_get_project_not_found():
    with patch("flip_api.model_services.save_model.get_project_by_id", return_value=None):
        yield


@pytest.fixture
def mock_get_project_not_approved():
    mock_project = MagicMock()
    mock_project.status = ProjectStatus.STAGED
    with patch("flip_api.model_services.save_model.get_project_by_id", return_value=mock_project):
        yield


# ---------- Test Cases ----------


def test_save_model_success(
    override_dependencies,
    mock_can_access_project_true,
    mock_get_project_approved,
):
    # Simulate approved trusts being returned
    override_dependencies.exec.return_value.all.return_value = [uuid4(), uuid4()]  # Approved trusts

    def add_mock(obj):
        if isinstance(obj, Model):  # set id on actual Model instance
            obj.id = test_model_id

    override_dependencies.add.side_effect = add_mock
    override_dependencies.flush.side_effect = lambda: None
    override_dependencies.begin.return_value.__enter__.return_value = None

    response = client.post("/model", json=test_payload)
    print(response.json())
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"id": str(test_model_id)}


def test_save_model_forbidden(mock_can_access_project_false):
    response = client.post("/model", json=test_payload)
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert "denied access" in response.json()["detail"]


def test_save_model_project_not_found(mock_can_access_project_true, mock_get_project_not_found):
    response = client.post("/model", json=test_payload)
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert "No project found" in response.json()["detail"]


def test_save_model_project_not_approved(mock_can_access_project_true, mock_get_project_not_approved):
    response = client.post("/model", json=test_payload)
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "is not approved" in response.json()["detail"]


def test_save_model_no_approved_trusts(mock_can_access_project_true, mock_get_project_approved, override_dependencies):
    override_dependencies.exec.return_value.all.return_value = []
    response = client.post("/model", json=test_payload)
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "No approved trusts found" in response.json()["detail"]


def test_save_model_database_error(mock_can_access_project_true, mock_get_project_approved, override_dependencies):
    override_dependencies.exec.return_value.all.return_value = [uuid4()]  # Approved trusts
    override_dependencies.add.side_effect = SQLAlchemyError

    response = client.post("/model", json=test_payload)
    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert "Database error" in response.json()["detail"]


def test_save_model_unexpected_error(mock_can_access_project_true, mock_get_project_approved, override_dependencies):
    override_dependencies.exec.return_value.all.return_value = [uuid4()]  # Approved trusts
    override_dependencies.add.side_effect = Exception("Unexpected error")

    response = client.post("/model", json=test_payload)
    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert "Unexpected error" in response.json()["detail"]
