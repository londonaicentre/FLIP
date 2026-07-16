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

import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlmodel import Session

from flip_api.auth.access_manager import authenticate_internal_service
from flip_api.db.models.main_models import Trust
from flip_api.domain.schemas.private import TrainingMetrics
from flip_api.main import app
from flip_api.private_services.services.private_service import save_training_metrics

# Test client to test the endpoint
client = TestClient(app)


@pytest.fixture
def fl_client_name():
    return "Example Trust"


@pytest.fixture
def trust():
    # The trust the FL client name resolves to (resolve_trust_from_fl_client_name returns this).
    return Trust(name="Example Trust")


@pytest.fixture
def mock_db_session_fixture():  # Renamed to avoid conflict with mock_db_session arg in client
    session = MagicMock(spec=Session)
    session.commit = MagicMock()
    session.rollback = MagicMock()
    session.add = MagicMock()
    return session


@pytest.fixture
def sample_metrics_payload_dict(fl_client_name):
    return {
        "fl_client_name": fl_client_name,
        "global_round": 5,
        "label": "example_label",
        "result": 0.85,
    }


@pytest.fixture
def sample_metrics_payload_obj(sample_metrics_payload_dict):
    return TrainingMetrics(**sample_metrics_payload_dict)


class TestTrainingMetricsModel:
    def test_payload_creation_success(self, sample_metrics_payload_dict, fl_client_name):
        metrics = TrainingMetrics(**sample_metrics_payload_dict)
        assert metrics.fl_client_name == fl_client_name
        assert metrics.result == 0.85

    def test_payload_missing_fl_client_name(self):
        with pytest.raises(ValidationError):
            TrainingMetrics(global_round=5)  # Missing fl_client_name

    def test_payload_missing_metrics_values(self, fl_client_name):
        with pytest.raises(ValidationError):
            TrainingMetrics(fl_client_name=fl_client_name)  # Missing metrics

    def test_payload_wrong_types(self):
        with pytest.raises(ValidationError):
            TrainingMetrics(fl_client_name=123, global_round="five", label=1, result="x")  # Wrong types

    def test_payload_rejects_camelcase_global_round(self, fl_client_name):
        # The camelCase alias was dropped — only snake_case global_round is accepted.
        with pytest.raises(ValidationError):
            TrainingMetrics(fl_client_name=fl_client_name, globalRound=1, label="accuracy", result=0.9)

    def test_payload_defaults_x_label_to_global_round(self, sample_metrics_payload_dict):
        # A client that doesn't send x_label falls back to the default axis label (back-compat).
        metrics = TrainingMetrics(**sample_metrics_payload_dict)
        assert metrics.x_label == "Global Rounds"

    def test_payload_accepts_custom_x_label(self, sample_metrics_payload_dict):
        metrics = TrainingMetrics(**sample_metrics_payload_dict, x_label="epoch")
        assert metrics.x_label == "epoch"

    def test_payload_defaults_x_value_to_global_round(self, sample_metrics_payload_dict):
        # Payloads from pre-x_value FL images carry no x_value; the point plots at its global round.
        metrics = TrainingMetrics(**sample_metrics_payload_dict)
        assert metrics.x_value == 5.0

    def test_payload_backfills_explicit_null_x_value(self, sample_metrics_payload_dict):
        metrics = TrainingMetrics(**sample_metrics_payload_dict, x_value=None)
        assert metrics.x_value == 5.0

    def test_payload_accepts_custom_float_x_value(self, sample_metrics_payload_dict):
        # The plot coordinate is arbitrary; global_round is untouched provenance (FLIP#148).
        metrics = TrainingMetrics(**sample_metrics_payload_dict, x_value=7.5)
        assert metrics.x_value == 7.5
        assert metrics.global_round == 5

    def test_payload_rejects_non_finite_x_value(self, sample_metrics_payload_dict):
        # nan/inf would break JSON-encoding the metrics response for the whole model.
        with pytest.raises(ValidationError):
            TrainingMetrics(**sample_metrics_payload_dict, x_value=float("nan"))


class TestServiceFunctions:
    def test_save_training_metrics_success(
        self, mock_db_session_fixture: MagicMock, sample_metrics_payload_obj: TrainingMetrics, trust, model_id
    ):
        save_training_metrics(model_id, trust, sample_metrics_payload_obj, mock_db_session_fixture)
        mock_db_session_fixture.add.assert_called_once()
        added_object = mock_db_session_fixture.add.call_args[0][0]
        # FLMetrics.trust is the trust's id; fl_client_name is the reported FL client name.
        assert added_object.trust == trust.id
        assert added_object.fl_client_name == sample_metrics_payload_obj.fl_client_name
        assert added_object.model_id == model_id
        assert added_object.global_round == sample_metrics_payload_obj.global_round
        assert added_object.label == sample_metrics_payload_obj.label
        assert added_object.result == sample_metrics_payload_obj.result
        assert added_object.x_value == sample_metrics_payload_obj.x_value
        assert added_object.x_label == sample_metrics_payload_obj.x_label

        mock_db_session_fixture.commit.assert_called_once()
        mock_db_session_fixture.rollback.assert_not_called()

    def test_save_training_metrics_exception(
        self, mock_db_session_fixture: MagicMock, sample_metrics_payload_obj: TrainingMetrics, trust, model_id
    ):
        mock_db_session_fixture.add.side_effect = Exception("DB write error")
        with pytest.raises(Exception, match="DB write error"):
            save_training_metrics(model_id, trust, sample_metrics_payload_obj, mock_db_session_fixture)
        mock_db_session_fixture.commit.assert_not_called()
        mock_db_session_fixture.rollback.assert_called_once()  # Assuming rollback in actual implementation

    def test_save_training_metrics_maps_custom_x_label(
        self, mock_db_session_fixture: MagicMock, sample_metrics_payload_dict, trust, model_id
    ):
        # A client-supplied x-axis label is persisted onto the FLMetrics row (FLIP#148).
        payload = TrainingMetrics(**sample_metrics_payload_dict, x_label="epoch")
        save_training_metrics(model_id, trust, payload, mock_db_session_fixture)
        added_object = mock_db_session_fixture.add.call_args[0][0]
        assert added_object.x_label == "epoch"

    def test_save_training_metrics_maps_custom_x_value_and_keeps_global_round(
        self, mock_db_session_fixture: MagicMock, sample_metrics_payload_dict, trust, model_id
    ):
        # The plot coordinate is persisted separately from the provenance global round (FLIP#148).
        payload = TrainingMetrics(**sample_metrics_payload_dict, x_value=7.5, x_label="epoch")
        save_training_metrics(model_id, trust, payload, mock_db_session_fixture)
        added_object = mock_db_session_fixture.add.call_args[0][0]
        assert added_object.x_value == 7.5
        assert added_object.global_round == 5

    def test_save_training_metrics_backfills_x_value_for_legacy_payload(
        self, mock_db_session_fixture: MagicMock, sample_metrics_payload_obj: TrainingMetrics, trust, model_id
    ):
        # sample_metrics_payload_obj carries no explicit x_value — the old-image wire shape.
        save_training_metrics(model_id, trust, sample_metrics_payload_obj, mock_db_session_fixture)
        added_object = mock_db_session_fixture.add.call_args[0][0]
        assert added_object.x_value == 5.0


class TestSaveTrainingMetricsEndpoint:
    @classmethod
    def setup_class(cls):
        cls.model_id = uuid.uuid4()
        cls.url = f"/api/model/{cls.model_id}/metrics"
        cls.headers = {"Authorization": "Bearer test-token"}
        app.dependency_overrides[authenticate_internal_service] = lambda: None

    @classmethod
    def teardown_class(cls):
        app.dependency_overrides = {}

    @patch("flip_api.private_services.save_training_metrics.save_training_metrics")
    @patch("flip_api.private_services.save_training_metrics.validate_trust_ids")
    @patch("flip_api.private_services.save_training_metrics.resolve_trust_from_fl_client_name")
    def test_save_metrics_success(
        self, mock_resolve, mock_validate_trust_ids, mock_save_metrics, sample_metrics_payload_dict, trust
    ):
        mock_resolve.return_value = trust
        mock_validate_trust_ids.return_value = True
        mock_save_metrics.return_value = None

        response = client.post(self.url, json=sample_metrics_payload_dict, headers=self.headers)

        assert response.status_code == 204
        mock_save_metrics.assert_called_once()
        # validate_trust_ids is called with the resolved trust's id.
        assert mock_validate_trust_ids.call_args.kwargs["trust_ids"] == [trust.id]
        # save_training_metrics receives the resolved Trust.
        assert mock_save_metrics.call_args.kwargs["trust"] is trust

    @patch("flip_api.private_services.save_training_metrics.validate_trust_ids")
    @patch("flip_api.private_services.save_training_metrics.resolve_trust_from_fl_client_name")
    def test_save_metrics_invalid_trust(
        self, mock_resolve, mock_validate_trust_ids, sample_metrics_payload_dict, trust
    ):
        mock_resolve.return_value = trust
        mock_validate_trust_ids.return_value = False

        response = client.post(self.url, json=sample_metrics_payload_dict, headers=self.headers)

        assert response.status_code == 400
        assert "trust" in response.json()["detail"].lower()

    @patch("flip_api.private_services.save_training_metrics.resolve_trust_from_fl_client_name")
    def test_save_metrics_unresolvable_fl_client(self, mock_resolve, sample_metrics_payload_dict):
        # The FL client name maps to no trust (e.g. an unassigned NVFLARE kit slot).
        mock_resolve.return_value = None

        response = client.post(self.url, json=sample_metrics_payload_dict, headers=self.headers)

        assert response.status_code == 400
        assert "could not be resolved" in response.json()["detail"].lower()

    @patch("flip_api.private_services.save_training_metrics.save_training_metrics")
    @patch("flip_api.private_services.save_training_metrics.validate_trust_ids")
    @patch("flip_api.private_services.save_training_metrics.resolve_trust_from_fl_client_name")
    def test_save_metrics_internal_error(
        self, mock_resolve, mock_validate_trust_ids, mock_save_metrics, sample_metrics_payload_dict, trust
    ):
        mock_resolve.return_value = trust
        mock_validate_trust_ids.return_value = True
        mock_save_metrics.side_effect = Exception("Simulated DB failure")

        response = client.post(self.url, json=sample_metrics_payload_dict, headers=self.headers)

        assert response.status_code == 500
        assert "internal server error" in response.json()["detail"].lower()

    def test_save_metrics_missing_token(self, sample_metrics_payload_dict):
        # Temporarily override to simulate auth failure
        def mock_auth():
            raise HTTPException(status_code=401, detail="Invalid token")

        app.dependency_overrides[authenticate_internal_service] = mock_auth

        response = client.post(self.url, json=sample_metrics_payload_dict)

        assert response.status_code == 401
        assert "invalid token" in response.json()["detail"].lower()

        # Restore good auth
        app.dependency_overrides[authenticate_internal_service] = lambda: None
