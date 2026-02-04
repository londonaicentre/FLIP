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
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.domain.interfaces.model import ILog
from flip_api.main import app

client = TestClient(app)

test_model_id = uuid4()
test_user_id = uuid4()
mock_logs = [
    ILog(
        id=uuid4(),
        model_id=test_model_id,
        log_date=datetime.now(),
        success=True,
        log="Log entry 1",
    ),  # type: ignore[call-arg]
    ILog(
        id=uuid4(),
        model_id=test_model_id,
        log_date=datetime.now(),
        success=False,
        log="Log entry 2",
    ),  # type: ignore[call-arg]
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
    with patch("flip_api.model_services.retrieve_logs_for_model.can_access_model", return_value=True):
        yield


@pytest.fixture
def mock_can_access_false():
    with patch("flip_api.model_services.retrieve_logs_for_model.can_access_model", return_value=False):
        yield


@pytest.fixture
def mock_model_status_ok():
    mock_status = MagicMock(deleted=False)
    with patch("flip_api.model_services.retrieve_logs_for_model.get_model_status", return_value=mock_status):
        yield


@pytest.fixture
def mock_model_status_deleted():
    mock_status = MagicMock(deleted=True)
    with patch("flip_api.model_services.retrieve_logs_for_model.get_model_status", return_value=mock_status):
        yield


@pytest.fixture
def mock_model_status_none():
    with patch("flip_api.model_services.retrieve_logs_for_model.get_model_status", return_value=None):
        yield


# ---------- Test Cases ----------


def test_retrieve_logs_for_model_success(override_dependencies, mock_can_access_true, mock_model_status_ok):
    # First call to exec() → used for model check (returns a model)
    mock_model = MagicMock()

    # Second call to exec().all() → used for retrieving logs
    mock_exec_result = MagicMock()
    mock_exec_result.all.return_value = mock_logs

    override_dependencies.exec.side_effect = [
        mock_model,  # db.exec(...).first() → model exists
        mock_exec_result,  # db.exec(...).all() → returns mock logs
    ]

    response = client.get(f"/model/{test_model_id}/logs")
    assert response.status_code == status.HTTP_200_OK
    assert isinstance(response.json(), list)
    assert len(response.json()) == 2


def test_retrieve_logs_for_model_forbidden(mock_can_access_false):
    response = client.get(f"/model/{test_model_id}/logs")
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert "denied access" in response.json()["detail"]


def test_retrieve_logs_for_model_model_not_found(mock_can_access_true, mock_model_status_none):
    response = client.get(f"/model/{test_model_id}/logs")
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert "does not exist" in response.json()["detail"]


def test_retrieve_logs_for_model_model_deleted(mock_can_access_true, mock_model_status_deleted):
    response = client.get(f"/model/{test_model_id}/logs")
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert "does not exist" in response.json()["detail"]


def test_retrieve_logs_for_model_orphaned_model(mock_can_access_true, mock_model_status_ok, override_dependencies):
    # First call to exec() → used for model check (returns a model)
    mock_model = MagicMock()
    mock_model.first.return_value = None  # Simulate that the model is orphaned

    override_dependencies.exec.side_effect = [
        mock_model,  # db.exec(...).first() → model exists
        [],  # won't be reached
    ]

    response = client.get(f"/model/{test_model_id}/logs")
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert "does not exist or is orphaned" in response.json()["detail"]


def test_retrieve_logs_for_model_database_error(mock_can_access_true, mock_model_status_ok, override_dependencies):
    override_dependencies.exec.side_effect = SQLAlchemyError
    response = client.get(f"/model/{test_model_id}/logs")
    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert "Database error" in response.json()["detail"]


def test_retrieve_logs_for_model_unexpected_error(mock_can_access_true, mock_model_status_ok, override_dependencies):
    override_dependencies.exec.side_effect = Exception("Unexpected error")
    response = client.get(f"/model/{test_model_id}/logs")
    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert "Unexpected error" in response.json()["detail"]
