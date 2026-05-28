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
from pydantic import ValidationError

from flip_api.db.models.main_models import Trust
from flip_api.domain.interfaces.fl import IInitiateTrainingInputPayload
from flip_api.fl_services.initiate_training import initiate_training


def _trust(name: str) -> Trust:
    """Build a Trust ORM row with a fixed id for deterministic test assertions."""
    return Trust(id=uuid4(), name=name)


@pytest.fixture
def mock_db():
    # initiate_training receives the session as an argument, so the test passes a plain mock.
    mock_db = MagicMock()
    # Default: the single trust name `client1` resolves to a Trust row. Tests that
    # exercise other shapes (unknown trust, multiple trusts) override
    # `mock_db.exec(...).all` themselves.
    mock_db.exec.return_value.all.side_effect = lambda: [_trust("client1")]
    return mock_db


@pytest.fixture
def model_id():
    return uuid4()


@pytest.fixture
def fake_request():
    req = MagicMock(spec=Request)
    req.scope = {"request_id": "req-id"}
    return req


@pytest.fixture
def mock_can_modify_model():
    with patch("flip_api.fl_services.initiate_training.can_modify_model") as mock:
        mock.return_value = True
        yield mock


@pytest.fixture
def mock_add_fl_job():
    with patch("flip_api.fl_services.initiate_training.add_fl_job") as mock:
        yield mock


@pytest.fixture
def mock_update_model_status():
    with patch("flip_api.fl_services.initiate_training.update_model_status") as mock:
        mock.return_value = True
        yield mock


@pytest.fixture
def mock_add_log():
    with patch("flip_api.fl_services.initiate_training.add_log") as mock:
        yield mock


def test_initiate_training_success(
    model_id, fake_request, mock_db, mock_can_modify_model, mock_add_fl_job, mock_update_model_status, mock_add_log
):
    payload = IInitiateTrainingInputPayload(trusts=["client1"])
    response = initiate_training(model_id, payload, fake_request, mock_db, user_id="user123")
    assert response is None  # Expecting no content response

    mock_add_fl_job.assert_called_once()
    # add_fl_job must receive resolved Trust ORM rows, not the raw payload.trusts strings.
    _, called_trusts, _ = mock_add_fl_job.call_args.args
    assert all(isinstance(t, Trust) for t in called_trusts)
    assert [t.name for t in called_trusts] == ["client1"]

    mock_update_model_status.assert_called_once()
    assert mock_add_log.call_count == 2
    # Audit log uses names extracted from the Trust rows, not the raw payload.
    second_log_msg = mock_add_log.call_args_list[1].args[1]
    assert "client1" in second_log_msg


def test_initiate_training_passes_full_trust_rows_to_add_fl_job(
    model_id, fake_request, mock_db, mock_can_modify_model, mock_add_fl_job, mock_update_model_status, mock_add_log
):
    trust_1 = _trust("Trust_1")
    trust_2 = _trust("Trust_2")
    mock_db.exec.return_value.all.side_effect = lambda: [trust_1, trust_2]

    payload = IInitiateTrainingInputPayload(trusts=["Trust_1", "Trust_2"])
    initiate_training(model_id, payload, fake_request, mock_db, user_id="user123")

    _, called_trusts, _ = mock_add_fl_job.call_args.args
    # Same Trust instances the DB returned — endpoint must not project to names before
    # handing off to internal services.
    assert called_trusts == [trust_1, trust_2]


def test_initiate_training_forbidden(model_id, fake_request, mock_db, mock_can_modify_model):
    mock_can_modify_model.return_value = False
    payload = IInitiateTrainingInputPayload(trusts=["client1"])

    with pytest.raises(HTTPException) as exc_info:
        initiate_training(model_id, payload, fake_request, mock_db, user_id="user123")
    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN


def test_initiate_training_model_not_found(
    model_id, fake_request, mock_db, mock_can_modify_model, mock_add_fl_job, mock_add_log
):
    with patch("flip_api.fl_services.initiate_training.update_model_status", return_value=False):
        payload = IInitiateTrainingInputPayload(trusts=["client1"])
        with pytest.raises(HTTPException) as exc_info:
            initiate_training(model_id, payload, fake_request, mock_db, user_id="user123")
        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND


def test_initiate_training_failure(model_id, fake_request, mock_db, mock_can_modify_model):
    with patch("flip_api.fl_services.initiate_training.add_fl_job", side_effect=Exception("Unexpected error")):
        payload = IInitiateTrainingInputPayload(trusts=["client1"])
        with pytest.raises(HTTPException) as exc_info:
            initiate_training(model_id, payload, fake_request, mock_db, user_id="user123")
        assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "Unexpected error" in exc_info.value.detail


def test_initiate_training_rejects_unknown_trusts(
    model_id, fake_request, mock_db, mock_can_modify_model, mock_add_fl_job
):
    mock_db.exec.return_value.all.side_effect = lambda: [_trust("client1")]
    payload = IInitiateTrainingInputPayload(trusts=["client1", "ghost"])

    with pytest.raises(HTTPException) as exc_info:
        initiate_training(model_id, payload, fake_request, mock_db, user_id="user123")

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert "ghost" in exc_info.value.detail
    mock_add_fl_job.assert_not_called()


class TestIInitiateTrainingInputPayloadSchema:
    def test_rejects_empty_trusts(self):
        with pytest.raises(ValidationError) as exc_info:
            IInitiateTrainingInputPayload(trusts=[])
        assert "at least 1 item" in str(exc_info.value)

    def test_rejects_duplicate_trusts(self):
        with pytest.raises(ValidationError) as exc_info:
            IInitiateTrainingInputPayload(trusts=["a", "a"])
        assert "unique" in str(exc_info.value)

    def test_rejects_unknown_fields(self):
        with pytest.raises(ValidationError) as exc_info:
            IInitiateTrainingInputPayload(trusts=["a"], extra_field="x")
        assert "extra_field" in str(exc_info.value)

    def test_accepts_valid_payload(self):
        payload = IInitiateTrainingInputPayload(trusts=["Trust_1", "Trust_2"])
        assert payload.trusts == ["Trust_1", "Trust_2"]
