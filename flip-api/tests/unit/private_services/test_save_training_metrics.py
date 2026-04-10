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

from flip_api.auth.access_manager import check_authorization_token
from flip_api.domain.schemas.private import TrainingMetrics
from flip_api.main import app
from flip_api.private_services.services.private_service import save_training_metrics

# Path for mocking ModelIdSchema if it's a custom validator like in add_log.py
# If ModelIdSchema is not used, this can be removed.
# MOCKED_MODEL_ID_SCHEMA_VALIDATE_PATH = "flip_api.private_services.save_training_metrics.ModelIdSchema.validate"
# MOCKED_IS_TRUST_ASSOCIATED_PATH = "flip_api.private_services.save_training_metrics.validate_trusts"
# MOCKED_STORE_METRICS_PATH = "flip_api.private_services.save_training_metrics._store_training_metrics_in_db"

# Test client to test the endpoint
client = TestClient(app)


@pytest.fixture
def trust():
    return "Example Trust"


@pytest.fixture
def mock_db_session_fixture():  # Renamed to avoid conflict with mock_db_session arg in client
    session = MagicMock(spec=Session)
    session.commit = MagicMock()
    session.rollback = MagicMock()
    session.add = MagicMock()
    session.execute.return_value.scalar_one_or_none.return_value = None
    return session


@pytest.fixture
def sample_metrics_payload_dict(trust):
    return {
        "trust": trust,
        "global_round": 5,
        "label": "example_label",
        "result": 0.85,
    }


@pytest.fixture
def sample_metrics_payload_obj(sample_metrics_payload_dict):
    return TrainingMetrics(**sample_metrics_payload_dict)


class TestTrainingMetricsModel:
    def test_payload_creation_success(self, sample_metrics_payload_dict, trust):
        metrics = TrainingMetrics(**sample_metrics_payload_dict)
        assert metrics.trust == trust
        assert metrics.result == 0.85

    def test_payload_missing_trust(self):
        with pytest.raises(ValidationError):
            TrainingMetrics(global_round=5)  # Missing trust

    def test_payload_missing_metrics_values(self, trust):
        with pytest.raises(ValidationError):
            TrainingMetrics(trust=trust)  # Missing metrics

    def test_payload_wrong_types(self):
        with pytest.raises(ValidationError):
            TrainingMetrics(trust=123, global_round="five", label=1, result="not_a_number")  # Wrong types


class TestServiceFunctions:
    def test_save_training_metrics_success(
        self, mock_db_session_fixture: MagicMock, sample_metrics_payload_obj: TrainingMetrics, model_id
    ):
        save_training_metrics(model_id, sample_metrics_payload_obj, mock_db_session_fixture)
        mock_db_session_fixture.add.assert_called_once()
        # Further assertions can be made on the object passed to add, e.g., its type and attributes
        added_object = mock_db_session_fixture.add.call_args[0][0]
        assert added_object.trust == sample_metrics_payload_obj.trust
        assert added_object.model_id == model_id
        assert added_object.global_round == sample_metrics_payload_obj.global_round
        assert added_object.label == sample_metrics_payload_obj.label
        assert added_object.result == sample_metrics_payload_obj.result

        mock_db_session_fixture.commit.assert_called_once()
        mock_db_session_fixture.rollback.assert_not_called()

    def test_save_training_metrics_exception(
        self, mock_db_session_fixture: MagicMock, sample_metrics_payload_obj: TrainingMetrics, model_id
    ):
        mock_db_session_fixture.add.side_effect = Exception("DB write error")
        with pytest.raises(Exception, match="DB write error"):
            save_training_metrics(model_id, sample_metrics_payload_obj, mock_db_session_fixture)
        mock_db_session_fixture.commit.assert_not_called()
        mock_db_session_fixture.rollback.assert_called_once()  # Assuming rollback in actual implementation


class TestSaveTrainingMetricsEndpoint:
    @classmethod
    def setup_class(cls):
        cls.model_id = uuid.uuid4()
        cls.url = f"/model/{cls.model_id}/metrics"
        cls.headers = {"Authorization": "Bearer test-token"}
        app.dependency_overrides[check_authorization_token] = lambda: "test-token"

    @classmethod
    def teardown_class(cls):
        app.dependency_overrides = {}

    @patch("flip_api.private_services.save_training_metrics.save_training_metrics")
    @patch("flip_api.private_services.save_training_metrics.validate_trusts")
    def test_save_metrics_success(self, mock_validate_trusts, mock_save_metrics, sample_metrics_payload_dict):
        mock_validate_trusts.return_value = True
        mock_save_metrics.return_value = None

        response = client.post(self.url, json=sample_metrics_payload_dict, headers=self.headers)

        assert response.status_code == 204
        mock_save_metrics.assert_called_once()

    @patch("flip_api.private_services.save_training_metrics.validate_trusts")
    def test_save_metrics_invalid_trust(self, mock_validate_trusts, sample_metrics_payload_dict):
        mock_validate_trusts.return_value = False

        response = client.post(self.url, json=sample_metrics_payload_dict, headers=self.headers)

        assert response.status_code == 400
        assert "trust" in response.json()["detail"].lower()

    @patch("flip_api.private_services.save_training_metrics.save_training_metrics")
    @patch("flip_api.private_services.save_training_metrics.validate_trusts")
    def test_save_metrics_internal_error(self, mock_validate_trusts, mock_save_metrics, sample_metrics_payload_dict):
        mock_validate_trusts.return_value = True
        mock_save_metrics.side_effect = Exception("Simulated DB failure")

        response = client.post(self.url, json=sample_metrics_payload_dict, headers=self.headers)

        assert response.status_code == 500
        assert "internal server error" in response.json()["detail"].lower()

    def test_save_metrics_missing_token(self, sample_metrics_payload_dict):
        # Temporarily override to simulate auth failure
        def mock_auth():
            raise HTTPException(status_code=401, detail="Invalid token")

        app.dependency_overrides[check_authorization_token] = mock_auth

        response = client.post(self.url, json=sample_metrics_payload_dict)

        assert response.status_code == 401
        assert "invalid token" in response.json()["detail"].lower()

        # Restore good auth
        app.dependency_overrides[check_authorization_token] = lambda: "test-token"
