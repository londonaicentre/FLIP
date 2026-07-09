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

from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.domain.interfaces.model import (
    IModelProgress,
    IModelProgressTrust,
    ProgressTrustState,
)
from flip_api.main import app

client = TestClient(app)

test_model_id = uuid4()
test_user_id = uuid4()

sample_progress = IModelProgress(  # type: ignore[call-arg]
    current_round=7,
    total_rounds=15,
    trusts=[
        IModelProgressTrust(  # type: ignore[call-arg]
            id=uuid4(),
            name="King's College Hospital",
            code="KCH",
            last_round=7,
            state=ProgressTrustState.RETURNED,
        )
    ],
)


@pytest.fixture(autouse=True)
def override_dependencies():
    mock_session = MagicMock()
    app.dependency_overrides[get_session] = lambda: mock_session
    app.dependency_overrides[verify_token] = lambda: test_user_id
    yield mock_session
    app.dependency_overrides.clear()


@pytest.fixture
def mock_can_access_true():
    with patch("flip_api.model_services.get_progress.can_access_model", return_value=True):
        yield


@pytest.fixture
def mock_model_status_ok():
    with patch(
        "flip_api.model_services.get_progress.get_model_status", return_value=MagicMock(deleted=False)
    ):
        yield


def test_get_progress_success(mock_can_access_true, mock_model_status_ok):
    with patch("flip_api.model_services.get_progress.get_model_progress", return_value=sample_progress):
        response = client.get(f"/api/model/{test_model_id}/progress")

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["currentRound"] == 7
    assert body["totalRounds"] == 15
    assert body["trusts"][0]["code"] == "KCH"
    assert body["trusts"][0]["state"] == "returned"
    assert body["trusts"][0]["lastRound"] == 7


def test_get_progress_forbidden():
    with patch("flip_api.model_services.get_progress.can_access_model", return_value=False):
        response = client.get(f"/api/model/{test_model_id}/progress")

    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_get_progress_model_not_found(mock_can_access_true):
    with patch("flip_api.model_services.get_progress.get_model_status", return_value=None):
        response = client.get(f"/api/model/{test_model_id}/progress")

    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_get_progress_deleted_model(mock_can_access_true):
    with patch(
        "flip_api.model_services.get_progress.get_model_status", return_value=MagicMock(deleted=True)
    ):
        response = client.get(f"/api/model/{test_model_id}/progress")

    assert response.status_code == status.HTTP_404_NOT_FOUND
