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
from fastapi.testclient import TestClient

from flip_api.domain.interfaces.model import IModelResponse
from flip.main import app
from flip.step_functions_services.retrieve_model_step_function import (
    get_session,
    verify_token,
)

client = TestClient(app)


@pytest.fixture
def model_id():
    return uuid4()


@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.fixture
def mock_user_id():
    return uuid4()


@pytest.fixture
def mock_model_response(model_id):
    return IModelResponse(
        model_id=model_id,
        model_name="Test Model",
        model_description="This is a test model",
        project_id=uuid4(),
        status="TRAINING_STARTED",
        query=None,
        files=[],
    ).model_dump(mode="json", by_alias=True)


# Fixture to patch retrieve_model_status_from_logs
# @pytest.fixture
# def mock_retrieve_model_status_from_logs():
#     with patch(
#         "flip.step_functions_services.retrieve_model_step_function.retrieve_model_status_from_logs"
#     ) as mock:
#         mock.return_value = {"modelStatus": "TRAINING_STARTED"}
#         yield mock


@pytest.fixture(autouse=True)
def override_dependencies():
    mock_session = MagicMock()
    user_id = uuid4()

    app.dependency_overrides[get_session] = lambda: mock_session
    app.dependency_overrides[verify_token] = lambda: user_id

    yield mock_session, user_id

    app.dependency_overrides = {}


@patch("flip.step_functions_services.retrieve_model_step_function.update_model_status")
@patch("flip.step_functions_services.retrieve_model_step_function.retrieve_model")
def test_retrieve_model_success(
    mock_retrieve_model,
    mock_update_status,
    model_id,
    mock_model_response,
):
    mock_update_status.return_value = True
    mock_retrieve_model.return_value = mock_model_response

    response = client.post(f"/step/model/{model_id}")

    assert response.status_code == 200
    assert response.json() == mock_model_response
    mock_update_status.assert_called_once()
    mock_retrieve_model.assert_called_once()


@patch("flip.step_functions_services.retrieve_model_step_function.update_model_status")
@patch("flip.step_functions_services.retrieve_model_step_function.retrieve_model")
def test_update_model_status_fails(
    mock_retrieve_model,
    mock_update_status,
    model_id,
):
    mock_update_status.return_value = False

    response = client.post(f"/step/model/{model_id}")

    assert response.status_code == 500
    assert response.json()["detail"] == "Failed to update model status"


@patch("flip.step_functions_services.retrieve_model_step_function.update_model_status")
@patch("flip.step_functions_services.retrieve_model_step_function.retrieve_model")
def test_retrieve_model_raises_exception(
    mock_retrieve_model,
    mock_update_status,
    model_id,
):
    mock_update_status.return_value = True
    mock_retrieve_model.side_effect = Exception("Database connection error")

    response = client.post(f"/step/model/{model_id}")

    assert response.status_code == 500
    assert "Failed to retrieve model" in response.json()["detail"]
