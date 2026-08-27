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

import pytest
from pydantic import ValidationError

from flip_api.domain.schemas.private import TrainingLog, TrainingMetrics
from flip_api.domain.schemas.types import FLLogEvent


class TestTrainingMetrics:
    """Ingest bounds on the hub's TrainingMetrics mirror (the full contract is pinned
    behaviourally in flip-utils, and the definitions by its AST mirror guard)."""

    @pytest.mark.parametrize("bad_label", ["", "x" * 65])
    def test_out_of_bounds_x_label_rejected(self, bad_label):
        """x_label is plot identity and the rendered axis title: empty or oversized labels are refused."""
        with pytest.raises(ValidationError):
            TrainingMetrics(fl_client_name="site-1", global_round=1, label="loss", result=1.0, x_label=bad_label)

    def test_x_label_at_the_length_bound_is_accepted(self):
        """A 64-char x_label sits exactly on the bound and must validate."""
        metrics = TrainingMetrics(fl_client_name="site-1", global_round=1, label="loss", result=1.0, x_label="x" * 64)
        assert metrics.x_label == "x" * 64


class TestTrainingLog:
    """The /model/{id}/logs ingest payload: free-text rows XOR typed event rows."""

    def test_legacy_free_text_shape_still_validates(self):
        """Old FL images keep POSTing {fl_client_name, log} — must stay valid."""
        payload = TrainingLog(fl_client_name="Trust_1", log="trust exception: ...")
        assert payload.log == "trust exception: ..."
        assert payload.event_type is None
        assert payload.success is True

    def test_event_shape_validates_without_log_text(self):
        """Typed events carry facts, never display text."""
        payload = TrainingLog(
            event_type=FLLogEvent.ROUND_STARTED,
            global_round=1,
            details={"total_rounds": 15},
        )
        assert payload.log is None
        assert payload.fl_client_name is None
        assert payload.details == {"total_rounds": 15}

    def test_trust_attributed_event_shape(self):
        payload = TrainingLog(
            fl_client_name="Trust_2",
            event_type=FLLogEvent.CLIENT_RESULT_RECEIVED,
            global_round=7,
            details={"size_bytes": 2400000},
        )
        assert payload.fl_client_name == "Trust_2"
        assert payload.global_round == 7

    def test_rejects_neither_log_nor_event(self):
        with pytest.raises(ValidationError, match="log.*event_type|event_type.*log"):
            TrainingLog(fl_client_name="Trust_1")

    def test_rejects_both_log_and_event(self):
        """A row is either free text or a typed event, never both."""
        with pytest.raises(ValidationError, match="log.*event_type|event_type.*log"):
            TrainingLog(
                fl_client_name="Trust_1",
                log="text",
                event_type=FLLogEvent.ROUND_STARTED,
                global_round=1,
            )

    def test_event_requires_global_round(self):
        """Every event in the vocabulary is round-scoped."""
        with pytest.raises(ValidationError, match="global_round"):
            TrainingLog(event_type=FLLogEvent.ROUND_AGGREGATED)

    def test_global_round_is_one_based(self):
        """Emission normalises to 1-based on both backends; 0 is a bug upstream."""
        with pytest.raises(ValidationError):
            TrainingLog(event_type=FLLogEvent.ROUND_STARTED, global_round=0)

    def test_global_round_above_pg_integer_max_is_rejected(self):
        """The fl_logs.global_round column is PG INTEGER: an oversized round must
        422 at validation rather than 500 at insert — uploaded app code can reach
        this endpoint with arbitrary ints via flip.send_event."""
        with pytest.raises(ValidationError):
            TrainingLog(event_type=FLLogEvent.ROUND_STARTED, global_round=2**31)

    def test_global_round_at_pg_integer_max_is_accepted(self):
        payload = TrainingLog(event_type=FLLogEvent.ROUND_STARTED, global_round=2**31 - 1)
        assert payload.global_round == 2**31 - 1

    def test_unknown_event_type_is_accepted_for_forward_compat(self):
        """The vocabulary is plain text end-to-end: a newer FL image's event is
        stored and served via the render fallback, never 422-rejected at ingest."""
        payload = TrainingLog(event_type="SOMETHING_NEW", global_round=3)
        assert payload.event_type == "SOMETHING_NEW"

    def test_queue_position_is_rejected_even_with_a_global_round(self):
        """QUEUE_POSITION is hub-emitted by definition. Requiring global_round does
        not keep it out (a spoofed payload can just carry one), so the validator
        refuses the event_type itself — otherwise the row would render in the feed
        and could perturb the scheduler's emit-on-change dedup."""
        with pytest.raises(ValidationError, match="QUEUE_POSITION"):
            TrainingLog(
                event_type=FLLogEvent.QUEUE_POSITION,
                global_round=1,
                details={"position": 1, "job_id": "some-job"},
            )

    @pytest.mark.parametrize("event_type", ["", "   "])
    def test_blank_event_type_is_rejected(self, event_type):
        with pytest.raises(ValidationError):
            TrainingLog(event_type=event_type, global_round=3)

    def test_oversized_details_is_rejected(self):
        """details is persisted verbatim into JSONB and flip.send_event is reachable
        from uploaded app code — an unbounded payload must 422 like an oversized
        event_type or global_round would, not land in the column."""
        with pytest.raises(ValidationError, match="details"):
            TrainingLog(event_type=FLLogEvent.ROUND_STARTED, global_round=1, details={"blob": "x" * 8192})

    def test_details_within_the_bound_is_accepted(self):
        details = {"blob": "x" * 4000}
        payload = TrainingLog(event_type=FLLogEvent.ROUND_STARTED, global_round=1, details=details)
        assert payload.details == details

    def test_success_flag_round_trips(self):
        payload = TrainingLog(fl_client_name="Trust_1", log="boom", success=False)
        assert payload.success is False
