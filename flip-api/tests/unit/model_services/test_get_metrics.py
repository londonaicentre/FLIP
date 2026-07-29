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
from flip_api.domain.interfaces.model import IModelMetrics, IModelMetricsData, IModelMetricsValue
from flip_api.main import app

client = TestClient(app)

test_model_id = str(uuid4())
test_user_id = uuid4()
test_metrics = [
    IModelMetrics(
        y_label="Accuracy",
        x_label="Epochs",
        metrics=[
            IModelMetricsData(
                data=[
                    IModelMetricsValue(x_value=1, y_value=0.85),
                    IModelMetricsValue(x_value=2, y_value=0.90),
                    IModelMetricsValue(x_value=3, y_value=0.95),
                ],
                series_label="Training Accuracy",
            )
        ],
    ),
    IModelMetrics(
        y_label="Loss",
        x_label="Epochs",
        metrics=[
            IModelMetricsData(
                data=[
                    IModelMetricsValue(x_value=1, y_value=0.85),
                    IModelMetricsValue(x_value=2, y_value=0.90),
                    IModelMetricsValue(x_value=3, y_value=0.95),
                ],
                series_label="Training Loss",
            )
        ],
    ),
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
    with patch("flip_api.model_services.get_metrics.can_access_model", return_value=True):
        yield


@pytest.fixture
def mock_can_access_false():
    with patch("flip_api.model_services.get_metrics.can_access_model", return_value=False):
        yield


@pytest.fixture
def mock_model_status_exists():
    mock_status = MagicMock()
    with patch("flip_api.model_services.get_metrics.get_model_status", return_value=mock_status):
        yield


@pytest.fixture
def mock_model_status_none():
    with patch("flip_api.model_services.get_metrics.get_model_status", return_value=None):
        yield


@pytest.fixture
def mock_get_metrics():
    with patch("flip_api.model_services.get_metrics.get_metrics", return_value=test_metrics) as mock:
        yield mock


# ---------- Test Cases ----------


def test_get_metrics_success(
    mock_can_access_true,
    mock_model_status_exists,
    mock_get_metrics,
):
    response = client.get(f"/api/model/{test_model_id}/metrics")
    assert response.status_code == HTTPStatus.OK
    # The wire format is camelCase (FastAPI serialises response_model by alias);
    # Python attribute names are snake_case. The UI reads these exact keys.
    assert response.json()[0] == test_metrics[0].model_dump(by_alias=True)
    assert response.json()[1] == test_metrics[1].model_dump(by_alias=True)
    first_series = response.json()[0]["metrics"][0]
    assert set(response.json()[0]) == {"yLabel", "xLabel", "metrics"}
    assert first_series["seriesLabel"] == "Training Accuracy"
    assert set(first_series["data"][0]) == {"xValue", "yValue"}
    mock_get_metrics.assert_called_once()


def test_get_metrics_forbidden(
    mock_can_access_false,
):
    response = client.get(f"/api/model/{test_model_id}/metrics")
    assert response.status_code == HTTPStatus.FORBIDDEN
    assert "denied access" in response.json()["detail"]


def test_get_metrics_model_not_found(
    mock_can_access_true,
    mock_model_status_none,
):
    response = client.get(f"/api/model/{test_model_id}/metrics")
    assert response.status_code == HTTPStatus.NOT_FOUND
    assert "does not exist" in response.json()["detail"]


def test_get_metrics_database_error(
    mock_can_access_true,
    mock_model_status_exists,
):
    with patch("flip_api.model_services.get_metrics.get_metrics", side_effect=SQLAlchemyError):
        response = client.get(f"/api/model/{test_model_id}/metrics")
        assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
        assert "Database error occurred while fetching metrics." == response.json()["detail"]


def test_get_metrics_unexpected_error(
    mock_can_access_true,
    mock_model_status_exists,
):
    with patch("flip_api.model_services.get_metrics.get_metrics", side_effect=RuntimeError("Boom")):
        response = client.get(f"/api/model/{test_model_id}/metrics")
        assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
        assert "Unexpected error occurred while fetching metrics" in response.json()["detail"]
