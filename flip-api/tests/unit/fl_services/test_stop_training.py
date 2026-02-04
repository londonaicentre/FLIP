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
from fastapi import HTTPException, Request

from flip_api.domain.schemas.status import ModelStatus
from flip.fl_services.stop_training import stop_training


@pytest.fixture
def model_id():
    return uuid4()


@pytest.fixture
def fake_request():
    request = MagicMock(spec=Request)
    request.scope = {"request_id": "req-123"}
    request.path_params = {"target": "SERVER", "clients": None}
    return request


@pytest.fixture
def mock_db():
    with patch("flip.fl_services.stop_training.get_session") as mock_get_session:
        mock_db = MagicMock()
        mock_get_session.return_value = mock_db
        yield mock_db


@pytest.fixture
def mock_can_access_model():
    with patch("flip.fl_services.stop_training.can_access_model") as mock:
        mock.return_value = True
        yield mock


@pytest.fixture
def user_id():
    return "user-123"


@pytest.fixture
def mock_abort_model_training():
    with patch("flip.fl_services.stop_training.abort_model_training") as mock:
        yield mock


@pytest.fixture
def mock_update_status():
    with patch("flip.fl_services.stop_training.update_model_status") as mock:
        yield mock


def test_stop_training_success(
    model_id, fake_request, mock_db, mock_can_access_model, user_id, mock_abort_model_training, mock_update_status
):
    result = stop_training(model_id, fake_request, mock_db, user_id)

    assert result == ({}, 204)
    mock_abort_model_training.assert_called_once_with(fake_request, model_id, mock_db)
    mock_update_status.assert_called_once_with(model_id, ModelStatus.STOPPED, mock_db)


def test_stop_training_forbidden(fake_request, model_id, mock_db, mock_can_access_model, user_id):
    mock_can_access_model.return_value = False

    with pytest.raises(HTTPException) as exc_info:
        stop_training(model_id, fake_request, mock_db, user_id)

    assert exc_info.value.status_code == 403
    assert "denied access" in exc_info.value.detail


def test_stop_training_failure(
    fake_request, model_id, mock_db, mock_can_access_model, user_id, mock_abort_model_training
):
    mock_abort_model_training.side_effect = Exception("oops")

    with pytest.raises(HTTPException) as exc_info:
        stop_training(model_id, fake_request, mock_db, user_id)

    assert exc_info.value.status_code == 500
    assert "An error occurred while stopping model training" in exc_info.value.detail
