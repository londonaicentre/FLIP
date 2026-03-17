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
from flip_api.main import app

client = TestClient(app)

test_model_id = uuid4()
test_user_id = "user-123"

# ---------- Dependency Overrides ----------


@pytest.fixture(autouse=True)
def override_dependencies():
    mock_session = MagicMock()
    app.dependency_overrides[get_session] = lambda: mock_session
    app.dependency_overrides[verify_token] = lambda: test_user_id
    yield mock_session
    app.dependency_overrides.clear()


@pytest.fixture
def mock_can_access_true():
    with patch("flip_api.model_services.delete_model.can_access_model", return_value=True):
        yield


@pytest.fixture
def mock_can_access_false():
    with patch("flip_api.model_services.delete_model.can_access_model", return_value=False):
        yield


@pytest.fixture
def mock_model_status_not_deleted():
    mock_status = MagicMock(deleted=False)
    with patch("flip_api.model_services.delete_model.get_model_status", return_value=mock_status):
        yield


@pytest.fixture
def mock_model_status_deleted():
    mock_status = MagicMock(deleted=True)
    with patch("flip_api.model_services.delete_model.get_model_status", return_value=mock_status):
        yield


@pytest.fixture
def mock_model_status_none():
    with patch("flip_api.model_services.delete_model.get_model_status", return_value=None):
        yield


@pytest.fixture
def mock_delete_model():
    with patch("flip_api.model_services.delete_model.delete_model") as mock:
        yield mock


@pytest.fixture
def mock_abort_training():
    with patch("flip_api.model_services.delete_model.abort_model_training") as mock:
        yield mock


# ---------- Test cases ----------


def test_delete_model_success(
    mock_can_access_true,
    mock_model_status_not_deleted,
    mock_delete_model,
    mock_abort_training,
):
    response = client.delete(f"/api/model/{test_model_id}")
    assert response.status_code == status.HTTP_204_NO_CONTENT
    mock_delete_model.assert_called_once()
    mock_abort_training.assert_called_once()


def test_delete_model_forbidden(
    mock_can_access_false,
):
    response = client.delete(f"/api/model/{test_model_id}")
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert "denied access" in response.json()["detail"]


def test_delete_model_not_found(
    mock_can_access_true,
    mock_model_status_none,
):
    response = client.delete(f"/api/model/{test_model_id}")
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert "does not exist" in response.json()["detail"]


def test_delete_model_already_deleted(
    mock_can_access_true,
    mock_model_status_deleted,
):
    response = client.delete(f"/api/model/{test_model_id}")
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "already deleted" in response.json()["detail"]


def test_delete_model_database_error(
    mock_can_access_true,
    mock_model_status_not_deleted,
    mock_abort_training,
):
    with patch("flip_api.model_services.delete_model.delete_model", side_effect=SQLAlchemyError):
        response = client.delete(f"/api/model/{test_model_id}")
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "Database error" in response.json()["detail"]


def test_delete_model_unexpected_error(
    mock_can_access_true,
    mock_model_status_not_deleted,
    mock_abort_training,
):
    with patch("flip_api.model_services.delete_model.delete_model", side_effect=Exception("Unexpected error")):
        response = client.delete(f"/api/model/{test_model_id}")
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "Unexpected error" in response.json()["detail"]
