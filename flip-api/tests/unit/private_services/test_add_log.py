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

from unittest.mock import patch

import pytest
from fastapi import HTTPException

from flip_api.domain.schemas.private import TrainingLog
from flip.private_services.add_log import add_log_endpoint
from flip.utils.logger import logger  # To assert logger calls

# Mock schema validation functions if they are complex or have side effects
# For this example, we'll patch them directly in the tests.

# Sample data
# TODO consider using UUID for model_id in the test: Note that because we don't call the endpoint via 'TestClient',
# there is no type validation on the model_id, therefore we can use a string instead of a UUID.
trust_name = "TestTrust"
model_id = "endpoint_model_1"


@pytest.fixture
def sample_training_log():
    """Fixture for a sample TrainingLog."""

    training_log = TrainingLog(
        trust=trust_name,
        log="Test log message",
    )
    return training_log


class TestAddLogEndpoint:
    """Tests for the add_log FastAPI endpoint."""

    @patch("flip.private_services.add_log.validate_trusts", return_value=True)
    @patch("flip.private_services.add_log.add_log")
    def test_add_log_success(self, mock_add_log, mock_validate_trusts, mock_db_session, sample_training_log):
        """Test successful log creation via the endpoint."""

        model_id = "endpoint_model_1"
        session = mock_db_session

        response = add_log_endpoint(model_id, sample_training_log, session, token="fake_token")

        mock_add_log.assert_called_once_with(model_id=model_id, log=sample_training_log.log, session=session)
        assert response == {"detail": "Created"}

    @patch("flip.private_services.add_log.validate_trusts", return_value=True)
    @patch("flip.private_services.add_log.add_log")
    def test_add_log_http_exception_from_add_log(
        self, mock_add_log, mock_validate_trusts, mock_db_session, sample_training_log
    ):
        """Test when add_log itself raises an HTTPException."""

        http_error = HTTPException(status_code=409, detail="Conflict in logging")
        mock_add_log.side_effect = http_error
        model_id = "model_log_conflict"
        session = mock_db_session

        with pytest.raises(HTTPException) as exc_info:
            add_log_endpoint(model_id, sample_training_log, session, token="fake_token")

        assert exc_info.value.status_code == 409
        assert exc_info.value.detail == "Conflict in logging"

    @patch("flip.private_services.add_log.validate_trusts", return_value=True)
    @patch("flip.private_services.add_log.add_log")
    @patch.object(logger, "error")
    def test_add_log_general_exception_from_add_log(
        self,
        mock_logger_error,
        mock_add_log,
        mock_validate_trusts,
        mock_db_session,
        sample_training_log,
    ):
        """Test when add_log raises a non-HTTP general Exception."""

        general_error = ValueError("Something unexpected happened in add_log")
        mock_add_log.side_effect = general_error
        model_id = "model_log_general_error"
        session = mock_db_session

        with pytest.raises(HTTPException) as exc_info:
            add_log_endpoint(model_id, sample_training_log, session, token="fake_token")

        assert exc_info.value.status_code == 500
        assert exc_info.value.detail == "An internal server error occurred while adding the log."
        # The error from add_log is logged by add_log itself.
        # The endpoint logs its own "Unhandled error" message.
        mock_logger_error.assert_called_once_with(
            f"Unhandled error in add_log endpoint for model {model_id}: {str(general_error)}", exc_info=True
        )

    def test_add_log_invalid_trust(self, mock_db_session, sample_training_log):
        """Test when the trust is not associated with the model."""

        model_id = "model_invalid_trust"
        session = mock_db_session

        # Mock the trust validation to return False
        with patch("flip.private_services.add_log.validate_trusts", return_value=False):
            with pytest.raises(HTTPException) as exc_info:
                add_log_endpoint(model_id, sample_training_log, session, token="fake_token")

            assert exc_info.value.status_code == 400
            assert (
                exc_info.value.detail
                == f"The trust: {sample_training_log.trust} is not associated with model: {model_id}"
            )
