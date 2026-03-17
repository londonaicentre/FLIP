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
from flip_api.domain.schemas.status import ModelStatus
from flip_api.main import app
from flip_api.utils.constants import SERVICE_UNAVAILABLE_MESSAGE

client = TestClient(app)

test_model_id = uuid4()
test_user_id = "user-123"

# ---------- Dependency Overrides ----------


@pytest.fixture(autouse=True)
def override_dependencies():
    mock_db = MagicMock()
    app.dependency_overrides[get_session] = lambda: mock_db
    app.dependency_overrides[verify_token] = lambda: test_user_id
    yield mock_db
    app.dependency_overrides.clear()


# ---------- Patch Fixtures ----------


@pytest.fixture
def mock_can_access_true():
    with patch("flip_api.model_services.update_model_status.can_access_model", return_value=True):
        yield


@pytest.fixture
def mock_can_access_false():
    with patch("flip_api.model_services.update_model_status.can_access_model", return_value=False):
        yield


@pytest.fixture
def mock_deployment_mode_disabled():
    with patch("flip_api.model_services.update_model_status.is_deployment_mode_enabled", return_value=False):
        yield


@pytest.fixture
def mock_deployment_mode_enabled():
    with patch("flip_api.model_services.update_model_status.is_deployment_mode_enabled", return_value=True):
        yield


@pytest.fixture
def mock_update_model_status_success():
    with patch("flip_api.model_services.update_model_status.update_model_status", return_value=True) as mock:
        yield mock


@pytest.fixture
def mock_update_model_status_not_found():
    with patch("flip_api.model_services.update_model_status.update_model_status", return_value=None):
        yield


@pytest.fixture
def mock_add_log():
    with patch("flip_api.model_services.update_model_status.add_log") as mock:
        yield mock


# ---------- Test Cases ----------


def test_update_model_status_success(
    mock_can_access_true,
    mock_deployment_mode_disabled,
    mock_update_model_status_success,
    mock_add_log,
):
    response = client.patch(f"/api/model/{test_model_id}/status/{ModelStatus.TRAINING_STARTED.value}")
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"success": "status set"}
    mock_update_model_status_success.assert_called_once()
    mock_add_log.assert_called_once()


def test_update_model_status_success_no_log(
    mock_can_access_true,
    mock_deployment_mode_disabled,
):
    with patch("flip_api.model_services.update_model_status.update_model_status", return_value=True):
        with patch("flip_api.model_services.update_model_status.add_log") as mock_add_log:
            response = client.patch(f"/api/model/{test_model_id}/status/{ModelStatus.PENDING.value}")
            assert response.status_code == status.HTTP_200_OK
            assert response.json() == {"success": "status set"}
            mock_add_log.assert_not_called()


def test_update_model_status_forbidden(
    mock_can_access_false,
    mock_deployment_mode_disabled,
):
    response = client.patch(f"/api/model/{test_model_id}/status/{ModelStatus.ERROR.value}")
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert "denied access" in response.json()["detail"]


# FIXME
# def test_update_model_status_no_user_id():
#     app.dependency_overrides[verify_token] = lambda: None
#     response = client.patch(f"/model/{test_model_id}/status/{ModelStatus.ERROR.value}")
#     assert response.status_code == status.HTTP_403_FORBIDDEN
#     assert "Authorization token is invalid" in response.json()["detail"]


def test_update_model_status_deployment_mode_enabled(
    mock_can_access_true,
    mock_deployment_mode_enabled,
):
    response = client.patch(f"/api/model/{test_model_id}/status/{ModelStatus.ERROR.value}")
    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert SERVICE_UNAVAILABLE_MESSAGE in response.json()["detail"]


def test_update_model_status_model_not_found(
    override_dependencies,
    mock_can_access_true,
    mock_deployment_mode_disabled,
    mock_update_model_status_not_found,
):
    override_dependencies.get.return_value = None
    response = client.patch(f"/api/model/{test_model_id}/status/{ModelStatus.ERROR.value}")
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert "does not exist" in response.json()["detail"]


def test_update_model_status_database_error(
    override_dependencies,
    mock_can_access_true,
    mock_deployment_mode_disabled,
):
    override_dependencies.get.side_effect = SQLAlchemyError
    response = client.patch(f"/api/model/{test_model_id}/status/{ModelStatus.ERROR.value}")
    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert "Database error" in response.json()["detail"]
