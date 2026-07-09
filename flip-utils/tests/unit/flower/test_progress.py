# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
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

"""Tests for flip.flower.progress — round-event emission helpers.

Like ``flip.flower.metrics``, the module only references Flower types behind a
``TYPE_CHECKING`` guard, so plain Mocks stand in for reply Messages.
"""

from unittest.mock import Mock

from flip.flower.progress import report_client_result, report_round_aggregated, report_round_started
from flip.schemas import FLLogEvent

VALID_MODEL_ID = "123e4567-e89b-12d3-a456-426614174000"


def _reply(site: str | None = "Trust_1", arrays_bytes: list[bytes] | None = None, has_error: bool = False) -> Mock:
    """A minimal stand-in for a Flower reply Message (see test_metrics.py)."""
    content: dict[str, object] = {}
    if site is not None:
        content["config"] = {"site": site}
    if arrays_bytes is not None:
        record = Mock()
        record.values.return_value = [Mock(data=b) for b in arrays_bytes]
        content["arrays"] = record

    msg = Mock()
    msg.has_error.return_value = has_error
    msg.content.get.side_effect = content.get
    msg.metadata.src_node_id = 42
    return msg


class TestReportRoundStarted:
    def test_emits_round_started_with_total(self):
        flip = Mock()

        report_round_started(flip, VALID_MODEL_ID, server_round=2, total_rounds=3)

        flip.send_event.assert_called_once_with(
            model_id=VALID_MODEL_ID,
            event_type=FLLogEvent.ROUND_STARTED,
            global_round=2,
            details={"total_rounds": 3},
        )

    def test_omits_unknown_total(self):
        flip = Mock()

        report_round_started(flip, VALID_MODEL_ID, server_round=1, total_rounds=None)

        flip.send_event.assert_called_once_with(
            model_id=VALID_MODEL_ID,
            event_type=FLLogEvent.ROUND_STARTED,
            global_round=1,
            details=None,
        )

    def test_emitter_failure_is_swallowed(self):
        """Telemetry must never break the strategy loop (LOCAL_DEV placeholder ids etc.)."""
        flip = Mock()
        flip.send_event.side_effect = ValueError("Invalid model ID")

        report_round_started(flip, "not-a-uuid", server_round=1, total_rounds=None)


class TestReportClientResult:
    def test_train_reply_reports_serialized_weight_size(self):
        flip = Mock()
        msg = _reply(site="Trust_2", arrays_bytes=[b"1234", b"56"])

        assert report_client_result(msg, server_round=2, model_id=VALID_MODEL_ID, flip=flip) is True

        flip.send_event.assert_called_once_with(
            model_id=VALID_MODEL_ID,
            event_type=FLLogEvent.CLIENT_RESULT_RECEIVED,
            global_round=2,
            client_name="Trust_2",
            details={"size_bytes": 6},
        )

    def test_evaluation_reply_without_arrays_is_sizeless(self):
        flip = Mock()
        msg = _reply(site="Trust_1", arrays_bytes=None)

        assert report_client_result(msg, server_round=1, model_id=VALID_MODEL_ID, flip=flip) is True

        flip.send_event.assert_called_once_with(
            model_id=VALID_MODEL_ID,
            event_type=FLLogEvent.CLIENT_RESULT_RECEIVED,
            global_round=1,
            client_name="Trust_1",
            details=None,
        )

    def test_errored_reply_is_not_reported(self):
        flip = Mock()
        msg = _reply(has_error=True)

        assert report_client_result(msg, server_round=1, model_id=VALID_MODEL_ID, flip=flip) is False

        flip.send_event.assert_not_called()

    def test_emitter_failure_is_swallowed_and_counts_as_returned(self):
        """The reply WAS received; a telemetry hiccup must not miscount it."""
        flip = Mock()
        flip.send_event.side_effect = RuntimeError("boom")
        msg = _reply()

        assert report_client_result(msg, server_round=1, model_id=VALID_MODEL_ID, flip=flip) is True


class TestReportRoundAggregated:
    def test_emits_counts(self):
        flip = Mock()

        report_round_aggregated(flip, VALID_MODEL_ID, server_round=2, returned=2, expected=3)

        flip.send_event.assert_called_once_with(
            model_id=VALID_MODEL_ID,
            event_type=FLLogEvent.ROUND_AGGREGATED,
            global_round=2,
            details={"returned": 2, "expected": 3},
        )

    def test_unknown_expected_omits_details(self):
        flip = Mock()

        report_round_aggregated(flip, VALID_MODEL_ID, server_round=2, returned=2, expected=None)

        flip.send_event.assert_called_once_with(
            model_id=VALID_MODEL_ID,
            event_type=FLLogEvent.ROUND_AGGREGATED,
            global_round=2,
            details=None,
        )

    def test_emitter_failure_is_swallowed(self):
        flip = Mock()
        flip.send_event.side_effect = RuntimeError("boom")

        report_round_aggregated(flip, VALID_MODEL_ID, server_round=1, returned=0, expected=0)
