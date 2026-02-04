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
from fastapi import HTTPException, Request, status

from flip_api.domain.interfaces.fl import IInitiateTrainingInputPayload
from flip.fl_services.initiate_training import initiate_training


@pytest.fixture
def mock_db():
    with patch("flip.fl_services.run_jobs.get_session") as mock_get_session:
        mock_db = MagicMock()
        mock_get_session.return_value = mock_db
        yield mock_db


@pytest.fixture
def model_id():
    return uuid4()


@pytest.fixture
def fake_request():
    req = MagicMock(spec=Request)
    req.scope = {"request_id": "req-id"}
    return req


@pytest.fixture
def mock_can_access_model():
    with patch("flip.fl_services.initiate_training.can_access_model") as mock:
        mock.return_value = True
        yield mock


@pytest.fixture
def mock_add_fl_job():
    with patch("flip.fl_services.initiate_training.add_fl_job") as mock:
        yield mock


@pytest.fixture
def mock_update_model_status():
    with patch("flip.fl_services.initiate_training.update_model_status") as mock:
        mock.return_value = True
        yield mock


@pytest.fixture
def mock_add_log():
    with patch("flip.fl_services.initiate_training.add_log") as mock:
        yield mock


def test_initiate_training_success(
    model_id, fake_request, mock_db, mock_can_access_model, mock_add_fl_job, mock_update_model_status, mock_add_log
):
    payload = IInitiateTrainingInputPayload(trusts=["client1"])
    response = initiate_training(model_id, payload, fake_request, mock_db, user_id="user123")
    assert response is None  # Expecting no content response
    mock_add_fl_job.assert_called_once()
    mock_update_model_status.assert_called_once()
    assert mock_add_log.call_count == 2


def test_initiate_training_forbidden(model_id, fake_request, mock_db, mock_can_access_model):
    mock_can_access_model.return_value = False
    payload = IInitiateTrainingInputPayload(trusts=["client1"])

    with pytest.raises(HTTPException) as exc_info:
        initiate_training(model_id, payload, fake_request, mock_db, user_id="user123")
    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN


def test_initiate_training_model_not_found(
    model_id, fake_request, mock_db, mock_can_access_model, mock_add_fl_job, mock_add_log
):
    with patch("flip.fl_services.initiate_training.update_model_status", return_value=False):
        payload = IInitiateTrainingInputPayload(trusts=["client1"])
        with pytest.raises(HTTPException) as exc_info:
            initiate_training(model_id, payload, fake_request, mock_db, user_id="user123")
        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND


def test_initiate_training_failure(model_id, fake_request, mock_db, mock_can_access_model):
    with patch("flip.fl_services.initiate_training.add_fl_job", side_effect=Exception("Unexpected error")):
        payload = IInitiateTrainingInputPayload(trusts=["client1"])
        with pytest.raises(HTTPException) as exc_info:
            initiate_training(model_id, payload, fake_request, mock_db, user_id="user123")
        assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "Unexpected error" in exc_info.value.detail
