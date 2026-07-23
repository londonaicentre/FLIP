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

from flip.flower.progress import (
    RoundTelemetry,
    report_client_result,
    report_round_aggregated,
    report_round_started,
    resolve_absent_site,
)
from flip.schemas import FLLogEvent

VALID_MODEL_ID = "123e4567-e89b-12d3-a456-426614174000"


def _reply(
    site: str | None = "Trust_1",
    arrays_bytes: list[bytes] | None = None,
    has_error: bool = False,
    src_node_id: int = 42,
    error: object | None = None,
) -> Mock:
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
    msg.error = error
    msg.content.get.side_effect = content.get
    msg.metadata.src_node_id = src_node_id
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

    def test_unattributable_healthy_reply_skips_the_event_but_still_counts(self):
        """No site → no hub row (a hub-level "someone uploaded" would be worse
        than silence), but the reply WAS received so the round count keeps it."""
        flip = Mock()
        msg = _reply(site=None)

        assert report_client_result(msg, server_round=1, model_id=VALID_MODEL_ID, flip=flip) is True

        flip.send_event.assert_not_called()


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


class TestResolveAbsentSite:
    """Identify a silent client by elimination, not by its reply's node id.

    Flower stamps a placeholder ``src_node_id`` (1) on the error reply it
    synthesises for an unreachable node, so the dead client cannot be recognised
    from the reply itself. The strategy does know which nodes it dispatched to
    and which answered, and it has learned each node's site from earlier healthy
    replies — that is enough to name the one that went missing.
    """

    SITES = {111: "Trust_1", 222: "Trust_2"}

    def test_single_absent_node_is_named(self):
        assert resolve_absent_site({111, 222}, {111}, self.SITES) == "Trust_2"

    def test_no_absent_node_yields_none(self):
        assert resolve_absent_site({111, 222}, {111, 222}, self.SITES) is None

    def test_ambiguous_absence_yields_none(self):
        """Two clients gone at once: guessing which is which would be a lie."""
        assert resolve_absent_site({111, 222}, set(), self.SITES) is None

    def test_absent_node_never_seen_before_yields_none(self):
        """A client that crashed before its first healthy reply has no known site."""
        assert resolve_absent_site({111, 333}, {111}, self.SITES) is None


class TestRoundTelemetry:
    """The per-phase dispatch bookkeeping behind FlipFedAvg.

    Extracted from the strategy (which needs flwr to import) precisely so CI can
    pin these behaviours: absent clients are named only from sites learned off
    healthy replies across the run, and each phase's absences resolve against
    that phase's own roster — a train strategy's evaluate arm may sample a
    different cohort.
    """

    def _telemetry_after_healthy_train_round(self, flip: Mock) -> RoundTelemetry:
        """Round 1 trains on nodes 10 (GSTT) and 20 (KCH); both sites get learned."""
        telemetry = RoundTelemetry()
        telemetry.record_dispatch("train", {10, 20})
        telemetry.forward_replies(
            [_reply(site="GSTT", src_node_id=10), _reply(site="KCH", src_node_id=20)],
            phase="train",
            server_round=1,
            model_id=VALID_MODEL_ID,
            flip=flip,
        )
        return telemetry

    def test_absent_client_is_named_by_elimination_within_its_phase(self):
        flip = Mock()
        telemetry = self._telemetry_after_healthy_train_round(flip)
        telemetry.record_dispatch("train", {10, 20})

        returned = telemetry.forward_replies(
            [
                _reply(site="GSTT", src_node_id=10),
                # Flower synthesises the error reply for the dead node with a
                # placeholder src_node_id, so only elimination can name it.
                _reply(site=None, src_node_id=1, has_error=True, error="boom"),
            ],
            phase="train",
            server_round=2,
            model_id=VALID_MODEL_ID,
            flip=flip,
        )

        assert returned == 1
        assert flip.send_handled_exception.call_args.kwargs["client_name"] == "KCH"

    def test_evaluate_absences_resolve_against_the_evaluate_roster_not_trains(self):
        """Node 10 was never sent an evaluate task, so it must not be blamed for an
        errored evaluate reply — nobody in the evaluate roster is actually absent."""
        flip = Mock()
        telemetry = self._telemetry_after_healthy_train_round(flip)  # train roster {10, 20}
        telemetry.record_dispatch("evaluate", {20})

        telemetry.forward_replies(
            [
                _reply(site="KCH", src_node_id=20),
                _reply(site=None, src_node_id=1, has_error=True, error="boom"),
            ],
            phase="evaluate",
            server_round=1,
            model_id=VALID_MODEL_ID,
            flip=flip,
        )

        assert flip.send_handled_exception.call_args.kwargs["client_name"] is None

    def test_dispatched_count_is_per_phase(self):
        telemetry = RoundTelemetry()
        telemetry.record_dispatch("train", {10, 20})
        telemetry.record_dispatch("evaluate", {20})

        assert telemetry.dispatched_count("train") == 2
        assert telemetry.dispatched_count("evaluate") == 1

    def test_dispatched_count_is_none_before_any_dispatch(self):
        assert RoundTelemetry().dispatched_count("evaluate") is None

    def test_re_dispatch_replaces_the_phase_roster(self):
        """Each round's dispatch overwrites the last — a node sampled out of round
        N must not linger from round N-1 and read as absent forever."""
        telemetry = RoundTelemetry()
        telemetry.record_dispatch("train", {10, 20})
        telemetry.record_dispatch("train", {10})

        assert telemetry.dispatched_count("train") == 1
