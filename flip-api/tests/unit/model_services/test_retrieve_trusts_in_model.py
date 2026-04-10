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
from flip_api.utils.constants import SERVICE_UNAVAILABLE_MESSAGE

client = TestClient(app)

test_model_id = uuid4()
test_user_id = uuid4()
mock_trust_result = [
    (uuid4(), "Trust A", "https://endpoint-a", "fl-client-A"),
    (uuid4(), "Trust B", "https://endpoint-b", "fl-client-B"),
]

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
    with patch("flip_api.model_services.retrieve_trusts_in_model.can_access_model", return_value=True):
        yield


@pytest.fixture
def mock_can_access_false():
    with patch("flip_api.model_services.retrieve_trusts_in_model.can_access_model", return_value=False):
        yield


@pytest.fixture
def mock_model_status_exists():
    status_result = MagicMock(deleted=False)
    with patch("flip_api.model_services.retrieve_trusts_in_model.get_model_status", return_value=status_result):
        yield


@pytest.fixture
def mock_model_status_deleted():
    status_result = MagicMock(deleted=True)
    with patch("flip_api.model_services.retrieve_trusts_in_model.get_model_status", return_value=status_result):
        yield


@pytest.fixture
def mock_model_status_none():
    with patch("flip_api.model_services.retrieve_trusts_in_model.get_model_status", return_value=None):
        yield


@pytest.fixture
def mock_deployment_mode_disabled():
    with patch("flip_api.model_services.retrieve_trusts_in_model.is_deployment_mode_enabled", return_value=False):
        yield


@pytest.fixture
def mock_deployment_mode_enabled():
    with patch("flip_api.model_services.retrieve_trusts_in_model.is_deployment_mode_enabled", return_value=True):
        yield


# ---------- Test Cases ----------


def test_get_trusts_success(
    override_dependencies,
    mock_can_access_true,
    mock_model_status_exists,
    mock_deployment_mode_disabled,
):
    mock_result = MagicMock()
    mock_result.all.return_value = mock_trust_result

    override_dependencies.exec.return_value = mock_result

    response = client.get(f"/model/{test_model_id}/trusts")
    assert response.status_code == status.HTTP_200_OK


def test_get_trusts_forbidden(
    mock_can_access_false,
    mock_model_status_exists,
    mock_deployment_mode_disabled,
):
    response = client.get(f"/model/{test_model_id}/trusts")
    print(response.json())
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert "denied access" in response.json()["detail"]


def test_get_trusts_deployment_mode_enabled(
    mock_can_access_true,
    mock_model_status_exists,
    mock_deployment_mode_enabled,
):
    response = client.get(f"/model/{test_model_id}/trusts")
    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert SERVICE_UNAVAILABLE_MESSAGE in response.json()["detail"]


def test_get_trusts_no_user_id():
    app.dependency_overrides[verify_token] = lambda: None
    response = client.get(f"/model/{test_model_id}/trusts")
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert "Authorization token is invalid" in response.json()["detail"]


def test_get_trusts_model_not_found(
    mock_can_access_true,
    mock_model_status_none,
    mock_deployment_mode_disabled,
):
    response = client.get(f"/model/{test_model_id}/trusts")
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert "does not exist" in response.json()["detail"]


def test_get_trusts_model_deleted(
    mock_can_access_true,
    mock_model_status_deleted,
    mock_deployment_mode_disabled,
):
    response = client.get(f"/model/{test_model_id}/trusts")
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert "does not exist" in response.json()["detail"]


def test_get_trusts_database_error(
    mock_can_access_true,
    mock_model_status_exists,
    mock_deployment_mode_disabled,
    override_dependencies,
):
    override_dependencies.exec.side_effect = SQLAlchemyError

    response = client.get(f"/model/{test_model_id}/trusts")
    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert "Database error" in response.json()["detail"]


def test_get_trusts_unexpected_error(
    mock_can_access_true,
    mock_model_status_exists,
    mock_deployment_mode_disabled,
    override_dependencies,
):
    override_dependencies.exec.side_effect = Exception("Something went wrong")

    response = client.get(f"/model/{test_model_id}/trusts")
    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert "Something went wrong" in response.json()["detail"]
