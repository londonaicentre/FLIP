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

from http import HTTPStatus
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.domain.interfaces.model import IDetailedModelStatus, ModelStatus
from flip_api.main import app

client = TestClient(app)

test_model_id = uuid4()
test_user_id = "user-123"
test_model_details = {
    "name": "Updated Model Name",
    "description": "Updated description",
    "hyperparameters": {"learning_rate": 0.001},
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
def mock_can_access_true():
    with patch("flip_api.model_services.edit_model.can_access_model", return_value=True):
        yield


@pytest.fixture
def mock_can_access_false():
    with patch("flip_api.model_services.edit_model.can_access_model", return_value=False):
        yield


@pytest.fixture
def mock_model_status_editable():
    # mock_status = MagicMock()
    mock_status = IDetailedModelStatus(deleted=False, status=ModelStatus.PENDING)
    with patch("flip_api.model_services.edit_model.get_model_status", return_value=mock_status):
        yield


@pytest.fixture
def mock_model_status_none():
    with patch("flip_api.model_services.edit_model.get_model_status", return_value=None):
        yield


@pytest.fixture
def mock_model_status_uneditable():
    mock_status = MagicMock(deleted=False, status=ModelStatus.TRAINING_STARTED)
    with patch("flip_api.model_services.edit_model.get_model_status", return_value=mock_status):
        yield


@pytest.fixture
def mock_model_status_deleted():
    mock_status = MagicMock(deleted=True, status=ModelStatus.PENDING)
    with patch("flip_api.model_services.edit_model.get_model_status", return_value=mock_status):
        yield


@pytest.fixture
def mock_edit_model():
    with patch("flip_api.model_services.edit_model.edit_model") as mock:
        yield mock


# ---------- Test Cases ----------


def test_edit_model_success(
    mock_can_access_true,
    mock_model_status_editable,
    mock_edit_model,
):
    response = client.put(f"/api/model/{test_model_id}", json=test_model_details)
    assert response.status_code == HTTPStatus.NO_CONTENT
    mock_edit_model.assert_called_once()


def test_edit_model_forbidden(
    mock_can_access_false,
):
    response = client.put(f"/api/model/{test_model_id}", json=test_model_details)
    assert response.status_code == HTTPStatus.FORBIDDEN
    assert "denied access" in response.json()["detail"]


def test_edit_model_not_found(
    mock_can_access_true,
    mock_model_status_none,
):
    response = client.put(f"/api/model/{test_model_id}", json=test_model_details)
    assert response.status_code == HTTPStatus.NOT_FOUND
    assert "does not exist" in response.json()["detail"]


def test_edit_model_deleted_status(
    mock_can_access_true,
    mock_model_status_deleted,
):
    response = client.put(f"/api/model/{test_model_id}", json=test_model_details)
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert "cannot be edited because it was deleted" in response.json()["detail"]


def test_edit_model_invalid_status(
    mock_can_access_true,
    mock_model_status_uneditable,
):
    response = client.put(f"/api/model/{test_model_id}", json=test_model_details)
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert "cannot be edited due to its current status" in response.json()["detail"]


def test_edit_model_database_error(
    mock_can_access_true,
    mock_model_status_editable,
):
    with patch("flip_api.model_services.edit_model.edit_model", side_effect=SQLAlchemyError):
        response = client.put(f"/api/model/{test_model_id}", json=test_model_details)
        assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
        assert "Database error" in response.json()["detail"]


def test_edit_model_unexpected_error(
    mock_can_access_true,
    mock_model_status_editable,
):
    with patch("flip_api.model_services.edit_model.edit_model", side_effect=Exception("Unexpected error")):
        response = client.put(f"/api/model/{test_model_id}", json=test_model_details)
        assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
        assert "Unexpected error" in response.json()["detail"]
