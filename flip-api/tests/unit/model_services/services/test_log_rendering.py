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

from uuid import uuid4

import pytest

from flip_api.db.models.main_models import FLLogs
from flip_api.domain.schemas.types import FLLogEvent
from flip_api.model_services.services.log_rendering import render_log


def _row(**kwargs) -> FLLogs:
    return FLLogs(model_id=uuid4(), success=True, **kwargs)


class TestRenderLog:
    """render_log is the single home of activity-feed English.

    The FL layer sends facts; wording lives here so copy changes are a
    flip-api redeploy, never an FL-image rebuild.
    """

    def test_free_text_rows_pass_through_verbatim(self):
        assert render_log(_row(log="trust exception: boom")) == "trust exception: boom"

    def test_round_started(self):
        row = _row(event_type=FLLogEvent.ROUND_STARTED.value, global_round=7, details={"total_rounds": 15})
        assert render_log(row) == "Round 7 initiated · global model dispatched"

    def test_client_result_with_size(self):
        row = _row(
            event_type=FLLogEvent.CLIENT_RESULT_RECEIVED.value,
            global_round=7,
            details={"size_bytes": 2411725},
        )
        assert render_log(row) == "Round 7 weights uploaded · 2.3 MB"

    @pytest.mark.parametrize(
        ("size_bytes", "rendered"),
        [
            (512, "512 B"),
            (2048, "2.0 KB"),
            (1610612736, "1.5 GB"),
        ],
    )
    def test_client_result_size_units(self, size_bytes, rendered):
        row = _row(
            event_type=FLLogEvent.CLIENT_RESULT_RECEIVED.value,
            global_round=2,
            details={"size_bytes": size_bytes},
        )
        assert render_log(row) == f"Round 2 weights uploaded · {rendered}"

    def test_client_result_without_size_is_neutral(self):
        """Evaluation replies carry no weights; the wording must not claim any."""
        row = _row(event_type=FLLogEvent.CLIENT_RESULT_RECEIVED.value, global_round=1, details=None)
        assert render_log(row) == "Round 1 results returned"

    def test_round_aggregated_with_counts(self):
        row = _row(
            event_type=FLLogEvent.ROUND_AGGREGATED.value,
            global_round=6,
            details={"returned": 4, "expected": 5},
        )
        assert render_log(row) == "Round 6 aggregated · 4 of 5 trusts returned"

    def test_round_aggregated_without_counts(self):
        row = _row(event_type=FLLogEvent.ROUND_AGGREGATED.value, global_round=6, details=None)
        assert render_log(row) == "Round 6 aggregated"

    def test_unknown_event_type_degrades_to_readable_fallback(self):
        """A future event type served by an older renderer must not crash the feed."""
        row = _row(event_type="SOMETHING_NEW", global_round=3, details=None)
        assert render_log(row) == "Round 3 · SOMETHING_NEW"

    def test_row_with_neither_text_nor_event_renders_empty(self):
        assert render_log(_row(log=None)) == ""

    @pytest.mark.parametrize(
        "size_bytes",
        ["2.3 MB", {"value": 12}, [1024], "nan", "inf"],
    )
    def test_client_result_with_unusable_size_degrades_to_sizeless_line(self, size_bytes):
        """details is untyped JSONB, so a bad value must degrade this row — never raise.

        Raising here would 500 the whole feed on every request once the row is
        stored (the endpoint serves all rows of a model together).
        """
        row = _row(
            event_type=FLLogEvent.CLIENT_RESULT_RECEIVED.value,
            global_round=7,
            details={"size_bytes": size_bytes},
        )
        assert render_log(row) == "Round 7 results returned"

    @pytest.mark.parametrize(
        ("returned", "expected"),
        [("two", 5), (4, {"n": 5}), ("4", "5"), (None, 5)],
    )
    def test_round_aggregated_with_unusable_counts_degrades_to_bare_line(self, returned, expected):
        row = _row(
            event_type=FLLogEvent.ROUND_AGGREGATED.value,
            global_round=6,
            details={"returned": returned, "expected": expected},
        )
        assert render_log(row) == "Round 6 aggregated"

    def test_queue_position(self):
        row = _row(event_type=FLLogEvent.QUEUE_POSITION.value, details={"position": 3, "job_id": str(uuid4())})
        assert render_log(row) == "Model Queued (3)"

    @pytest.mark.parametrize(
        "details",
        [None, {}, {"position": "3"}, {"position": True}, {"position": 0}, {"position": -1}, {"position": 2.5}],
    )
    def test_queue_position_with_unusable_position_degrades_positionless(self, details):
        """A row with an unusable stored position must not invent one."""
        row = _row(event_type=FLLogEvent.QUEUE_POSITION.value, details=details)
        assert render_log(row) == "Model Queued"
