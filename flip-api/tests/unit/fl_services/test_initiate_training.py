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
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException, Request, status
from pydantic import ValidationError

from flip_api.db.models.main_models import Trust
from flip_api.domain.interfaces.fl import IInitiateTrainingInputPayload
from flip_api.fl_services.initiate_training import initiate_training
from flip_api.utils.constants import SERVICE_UNAVAILABLE_MESSAGE


def _trust(name: str, trust_id: UUID | None = None) -> Trust:
    """Build a Trust ORM row with a fixed id for deterministic test assertions."""
    return Trust(id=trust_id or uuid4(), name=name)


@pytest.fixture
def client1():
    # The default trust the mock DB resolves; tests build their payload from its id.
    return _trust("client1")


@pytest.fixture
def mock_db(client1):
    # initiate_training receives the session as an argument, so the test passes a plain mock.
    mock_db = MagicMock()
    # Default: the lookup resolves to a single trust (`client1`). Tests that exercise
    # other shapes (unknown trust, multiple trusts) override `mock_db.exec(...).all`.
    mock_db.exec.return_value.all.side_effect = lambda: [client1]
    return mock_db


@pytest.fixture
def model_id():
    return uuid4()


@pytest.fixture
def fake_request():
    req = MagicMock(spec=Request)
    req.scope = {"request_id": "req-id"}
    return req


@pytest.fixture(autouse=True)
def deployment_mode_disabled():
    # With a MagicMock session the real is_deployment_mode_enabled would read a
    # truthy row and 503 every request, so default it to disabled here; the
    # deployment-mode test below overrides the return value.
    with patch("flip_api.fl_services.initiate_training.is_deployment_mode_enabled", return_value=False) as mock_enabled:
        yield mock_enabled


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


@pytest.fixture
def mock_log_queue_positions():
    with patch("flip_api.fl_services.initiate_training.log_queue_positions") as mock:
        yield mock


def test_initiate_training_success(
    model_id, fake_request, mock_db, client1, mock_can_modify_model, mock_add_fl_job, mock_update_model_status,
    mock_add_log, mock_log_queue_positions
):
    payload = IInitiateTrainingInputPayload(trust_ids=[client1.id])
    response = initiate_training(model_id, payload, fake_request, mock_db, user_id="user123")
    assert response is None  # Expecting no content response

    mock_add_fl_job.assert_called_once()
    # add_fl_job must receive resolved Trust ORM rows, not the raw payload.trust_ids.
    _, called_trusts, _ = mock_add_fl_job.call_args.args
    assert all(isinstance(t, Trust) for t in called_trusts)
    assert [t.name for t in called_trusts] == ["client1"]

    mock_update_model_status.assert_called_once()
    # The enqueue announcement is a typed queue-position row ("Model Queued (n)"),
    # emitted via log_queue_positions; add_log carries only the trusts audit line.
    mock_log_queue_positions.assert_called_once_with(mock_db)
    assert mock_add_log.call_count == 1
    # Audit log uses names extracted from the resolved Trust rows, not the raw payload ids.
    trusts_log_msg = mock_add_log.call_args_list[0].args[1]
    assert "client1" in trusts_log_msg


def test_initiate_training_passes_full_trust_rows_to_add_fl_job(
    model_id, fake_request, mock_db, mock_can_modify_model, mock_add_fl_job, mock_update_model_status, mock_add_log
):
    trust_1 = _trust("Trust_1")
    trust_2 = _trust("Trust_2")
    mock_db.exec.return_value.all.side_effect = lambda: [trust_1, trust_2]

    payload = IInitiateTrainingInputPayload(trust_ids=[trust_1.id, trust_2.id])
    initiate_training(model_id, payload, fake_request, mock_db, user_id="user123")

    _, called_trusts, _ = mock_add_fl_job.call_args.args
    # Same Trust instances the DB returned — endpoint must not project to names/ids before
    # handing off to internal services.
    assert called_trusts == [trust_1, trust_2]


def test_initiate_training_forbidden(model_id, fake_request, mock_db, mock_can_modify_model):
    mock_can_modify_model.return_value = False
    payload = IInitiateTrainingInputPayload(trust_ids=[uuid4()])

    with pytest.raises(HTTPException) as exc_info:
        initiate_training(model_id, payload, fake_request, mock_db, user_id="user123")
    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN


def test_initiate_training_model_not_found(
    model_id, fake_request, mock_db, client1, mock_can_modify_model, mock_add_fl_job, mock_add_log
):
    with patch("flip_api.fl_services.initiate_training.update_model_status", return_value=False):
        payload = IInitiateTrainingInputPayload(trust_ids=[client1.id])
        with pytest.raises(HTTPException) as exc_info:
            initiate_training(model_id, payload, fake_request, mock_db, user_id="user123")
        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND


def test_initiate_training_failure(model_id, fake_request, mock_db, client1, mock_can_modify_model):
    with patch("flip_api.fl_services.initiate_training.add_fl_job", side_effect=Exception("Unexpected error")):
        payload = IInitiateTrainingInputPayload(trust_ids=[client1.id])
        with pytest.raises(HTTPException) as exc_info:
            initiate_training(model_id, payload, fake_request, mock_db, user_id="user123")
        assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "Unexpected error" in exc_info.value.detail


def test_initiate_training_unavailable_in_deployment_mode(
    model_id, fake_request, mock_db, client1, deployment_mode_disabled, mock_can_modify_model, mock_add_fl_job
):
    """Deployment mode enabled → 503, and nothing is queued."""
    deployment_mode_disabled.return_value = True
    payload = IInitiateTrainingInputPayload(trust_ids=[client1.id])

    with pytest.raises(HTTPException) as exc_info:
        initiate_training(model_id, payload, fake_request, mock_db, user_id="user123")

    assert exc_info.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert exc_info.value.detail == SERVICE_UNAVAILABLE_MESSAGE
    mock_add_fl_job.assert_not_called()


def test_initiate_training_rejects_unknown_trusts(
    model_id, fake_request, mock_db, client1, mock_can_modify_model, mock_add_fl_job
):
    # The DB resolves only `client1`; the payload also references an id with no Trust row.
    ghost_id = uuid4()
    payload = IInitiateTrainingInputPayload(trust_ids=[client1.id, ghost_id])

    with pytest.raises(HTTPException) as exc_info:
        initiate_training(model_id, payload, fake_request, mock_db, user_id="user123")

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert str(ghost_id) in exc_info.value.detail
    mock_add_fl_job.assert_not_called()


class TestIInitiateTrainingInputPayloadSchema:
    def test_rejects_empty_trust_ids(self):
        with pytest.raises(ValidationError) as exc_info:
            IInitiateTrainingInputPayload(trust_ids=[])
        assert "at least 1 item" in str(exc_info.value)

    def test_rejects_duplicate_trust_ids(self):
        dup = uuid4()
        with pytest.raises(ValidationError) as exc_info:
            IInitiateTrainingInputPayload(trust_ids=[dup, dup])
        assert "unique" in str(exc_info.value)

    def test_rejects_unknown_fields(self):
        with pytest.raises(ValidationError) as exc_info:
            IInitiateTrainingInputPayload(trust_ids=[uuid4()], extra_field="x")
        assert "extra_field" in str(exc_info.value)

    def test_rejects_non_uuid_entries(self):
        # Trust *names* (or any non-UUID string) are no longer accepted — identity is the id.
        with pytest.raises(ValidationError):
            IInitiateTrainingInputPayload(trust_ids=["Trust_1"])

    def test_accepts_valid_payload(self):
        id_1, id_2 = uuid4(), uuid4()
        payload = IInitiateTrainingInputPayload(trust_ids=[id_1, id_2])
        assert payload.trust_ids == [id_1, id_2]
