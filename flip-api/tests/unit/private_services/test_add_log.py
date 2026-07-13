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

from flip_api.db.models.main_models import Trust
from flip_api.domain.schemas.private import TrainingLog
from flip_api.domain.schemas.types import FLLogEvent
from flip_api.private_services.add_log import add_log_endpoint
from flip_api.utils.logger import logger  # To assert logger calls

# Sample data
# TODO consider using UUID for model_id in the test: Note that because we don't call the endpoint via 'TestClient',
# there is no type validation on the model_id, therefore we can use a string instead of a UUID.
fl_client_name = "TestTrust"
model_id = "endpoint_model_1"

# The trust the FL client name resolves to (resolve_trust_from_fl_client_name is mocked to return this).
resolved_trust = Trust(name="TestTrust")


@pytest.fixture
def sample_training_log():
    """Fixture for a sample TrainingLog."""

    training_log = TrainingLog(
        fl_client_name=fl_client_name,
        log="Test log message",
    )
    return training_log


class TestAddLogEndpoint:
    """Tests for the add_log FastAPI endpoint."""

    @patch("flip_api.private_services.add_log.resolve_trust_from_fl_client_name", return_value=resolved_trust)
    @patch("flip_api.private_services.add_log.validate_trust_ids", return_value=True)
    @patch("flip_api.private_services.add_log.add_log")
    def test_add_log_success(
        self, mock_add_log, mock_validate_trust_ids, mock_resolve, mock_db_session, sample_training_log
    ):
        """Test successful log creation via the endpoint."""

        model_id = "endpoint_model_1"
        session = mock_db_session

        response = add_log_endpoint(model_id, sample_training_log, session)

        mock_add_log.assert_called_once_with(
            model_id=model_id,
            log=sample_training_log.log,
            session=session,
            success=True,
            trust=resolved_trust,
            fl_client_name=fl_client_name,
            event_type=None,
            global_round=None,
            details=None,
        )
        assert response == {"detail": "Created"}
        # validate_trust_ids is called with the resolved trust's id.
        assert mock_validate_trust_ids.call_args.kwargs["trust_ids"] == [resolved_trust.id]

    @patch("flip_api.private_services.add_log.resolve_trust_from_fl_client_name", return_value=resolved_trust)
    @patch("flip_api.private_services.add_log.validate_trust_ids", return_value=True)
    @patch("flip_api.private_services.add_log.add_log")
    def test_add_log_http_exception_from_add_log(
        self, mock_add_log, mock_validate_trust_ids, mock_resolve, mock_db_session, sample_training_log
    ):
        """Test when add_log itself raises an HTTPException."""

        http_error = HTTPException(status_code=409, detail="Conflict in logging")
        mock_add_log.side_effect = http_error
        model_id = "model_log_conflict"
        session = mock_db_session

        with pytest.raises(HTTPException) as exc_info:
            add_log_endpoint(model_id, sample_training_log, session)

        assert exc_info.value.status_code == 409
        assert exc_info.value.detail == "Conflict in logging"

    @patch("flip_api.private_services.add_log.resolve_trust_from_fl_client_name", return_value=resolved_trust)
    @patch("flip_api.private_services.add_log.validate_trust_ids", return_value=True)
    @patch("flip_api.private_services.add_log.add_log")
    @patch.object(logger, "error")
    def test_add_log_general_exception_from_add_log(
        self,
        mock_logger_error,
        mock_add_log,
        mock_validate_trust_ids,
        mock_resolve,
        mock_db_session,
        sample_training_log,
    ):
        """Test when add_log raises a non-HTTP general Exception."""

        general_error = ValueError("Something unexpected happened in add_log")
        mock_add_log.side_effect = general_error
        model_id = "model_log_general_error"
        session = mock_db_session

        with pytest.raises(HTTPException) as exc_info:
            add_log_endpoint(model_id, sample_training_log, session)

        assert exc_info.value.status_code == 500
        assert exc_info.value.detail == "An internal server error occurred while adding the log."
        # The error from add_log is logged by add_log itself.
        # The endpoint logs its own "Unhandled error" message.
        mock_logger_error.assert_called_once_with(
            f"Unhandled error in add_log endpoint for model {model_id}: {str(general_error)}", exc_info=True
        )

    @patch("flip_api.private_services.add_log.resolve_trust_from_fl_client_name", return_value=resolved_trust)
    @patch("flip_api.private_services.add_log.validate_trust_ids", return_value=False)
    def test_add_log_invalid_trust(self, mock_validate_trust_ids, mock_resolve, mock_db_session, sample_training_log):
        """Test when the trust is not associated with the model."""

        model_id = "model_invalid_trust"
        session = mock_db_session

        with pytest.raises(HTTPException) as exc_info:
            add_log_endpoint(model_id, sample_training_log, session)

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == f"The trust: {resolved_trust.name} is not associated with model: {model_id}"

    @patch("flip_api.private_services.add_log.resolve_trust_from_fl_client_name", return_value=None)
    def test_add_log_unresolvable_fl_client(self, mock_resolve, mock_db_session, sample_training_log):
        """An FL client name that maps to no trust is rejected with 400."""

        model_id = "model_unresolvable"
        session = mock_db_session

        with pytest.raises(HTTPException) as exc_info:
            add_log_endpoint(model_id, sample_training_log, session)

        assert exc_info.value.status_code == 400
        assert "could not be resolved" in exc_info.value.detail.lower()

    @patch("flip_api.private_services.add_log.resolve_trust_from_fl_client_name", return_value=resolved_trust)
    @patch("flip_api.private_services.add_log.validate_trust_ids", return_value=True)
    @patch("flip_api.private_services.add_log.add_log")
    def test_success_false_is_persisted(self, mock_add_log, mock_validate, mock_resolve, mock_db_session):
        """A failed log (handled exception) must not default back to success=True on write."""

        failed_log = TrainingLog(fl_client_name=fl_client_name, log="trust exception: boom", success=False)

        add_log_endpoint(model_id, failed_log, mock_db_session)

        assert mock_add_log.call_args.kwargs["success"] is False

    @patch("flip_api.private_services.add_log.resolve_trust_from_fl_client_name")
    @patch("flip_api.private_services.add_log.add_log")
    def test_hub_event_skips_trust_resolution(self, mock_add_log, mock_resolve, mock_db_session):
        """A hub-attributed event (fl_client_name=None) never touches the slot-to-trust lookup."""

        hub_event = TrainingLog(
            event_type=FLLogEvent.ROUND_STARTED,
            global_round=7,
            details={"total_rounds": 15},
        )

        response = add_log_endpoint(model_id, hub_event, mock_db_session)

        assert response == {"detail": "Created"}
        mock_resolve.assert_not_called()
        kwargs = mock_add_log.call_args.kwargs
        assert kwargs["trust"] is None
        assert kwargs["fl_client_name"] is None
        assert kwargs["event_type"] == FLLogEvent.ROUND_STARTED
        assert kwargs["global_round"] == 7
        assert kwargs["details"] == {"total_rounds": 15}

    @patch("flip_api.private_services.add_log.resolve_trust_from_fl_client_name", return_value=resolved_trust)
    @patch("flip_api.private_services.add_log.validate_trust_ids", return_value=True)
    @patch("flip_api.private_services.add_log.add_log")
    def test_trust_event_forwards_event_fields(self, mock_add_log, mock_validate, mock_resolve, mock_db_session):
        """A trust-attributed event resolves the trust exactly like free-text logs do."""

        trust_event = TrainingLog(
            fl_client_name=fl_client_name,
            event_type=FLLogEvent.CLIENT_RESULT_RECEIVED,
            global_round=3,
            details={"size_bytes": 2400000},
        )

        add_log_endpoint(model_id, trust_event, mock_db_session)

        kwargs = mock_add_log.call_args.kwargs
        assert kwargs["trust"] is resolved_trust
        assert kwargs["event_type"] == FLLogEvent.CLIENT_RESULT_RECEIVED
        assert kwargs["global_round"] == 3
        assert kwargs["details"] == {"size_bytes": 2400000}

    @patch("flip_api.private_services.add_log.add_log")
    def test_unknown_event_type_is_stored_not_rejected(self, mock_add_log, mock_db_session):
        """A newer FL image's event must reach persistence: the vocabulary is plain
        text end-to-end, and the renderer degrades unknown events at serve time."""

        newer_image_event = TrainingLog(event_type="ROUND_CHECKPOINTED", global_round=4)

        response = add_log_endpoint(model_id, newer_image_event, mock_db_session)

        assert response == {"detail": "Created"}
        assert mock_add_log.call_args.kwargs["event_type"] == "ROUND_CHECKPOINTED"

    @patch("flip_api.private_services.add_log.resolve_trust_from_fl_client_name", return_value=None)
    @patch("flip_api.private_services.add_log.add_log")
    def test_unresolvable_error_report_is_kept_model_level(self, mock_add_log, mock_resolve, mock_db_session):
        """A traceback must never be dropped: uploaded apps control the reported site
        name, and a 400 here would strand the error in fl-server container logs while
        the user's model sits red. Persist model-level, naming the sender in the text."""

        error_report = TrainingLog(fl_client_name="mystery-host", log="trust exception: boom", success=False)

        response = add_log_endpoint(model_id, error_report, mock_db_session)

        assert response == {"detail": "Created"}
        kwargs = mock_add_log.call_args.kwargs
        assert kwargs["success"] is False
        assert kwargs["trust"] is None
        assert "mystery-host" in kwargs["log"]
        assert "trust exception: boom" in kwargs["log"]

    @patch("flip_api.private_services.add_log.resolve_trust_from_fl_client_name", return_value=resolved_trust)
    @patch("flip_api.private_services.add_log.validate_trust_ids", return_value=False)
    @patch("flip_api.private_services.add_log.add_log")
    def test_unassociated_trust_error_report_is_kept_model_level(
        self, mock_add_log, mock_validate, mock_resolve, mock_db_session
    ):
        error_report = TrainingLog(fl_client_name=fl_client_name, log="trust exception: boom", success=False)

        response = add_log_endpoint(model_id, error_report, mock_db_session)

        assert response == {"detail": "Created"}
        kwargs = mock_add_log.call_args.kwargs
        assert kwargs["success"] is False
        assert kwargs["trust"] is None
        assert "trust exception: boom" in kwargs["log"]

    @patch("flip_api.private_services.add_log.resolve_trust_from_fl_client_name", return_value=None)
    def test_unresolvable_typed_event_still_400s(self, mock_resolve, mock_db_session):
        """Typed events carry no traceback; rejecting a misattributed count is safer
        than storing it against nobody."""

        trust_event = TrainingLog(
            fl_client_name="mystery-host",
            event_type=FLLogEvent.CLIENT_RESULT_RECEIVED,
            global_round=2,
        )

        with pytest.raises(HTTPException) as exc_info:
            add_log_endpoint(model_id, trust_event, mock_db_session)

        assert exc_info.value.status_code == 400
