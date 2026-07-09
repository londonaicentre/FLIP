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

from datetime import datetime, timedelta
from uuid import uuid4

from flip_api.db.models.main_models import FLLogs
from flip_api.domain.interfaces.model import ITrustSummary, ProgressTrustState
from flip_api.domain.schemas.types import FLLogEvent
from flip_api.model_services.services.progress import derive_progress

MODEL_ID = uuid4()
T0 = datetime(2026, 7, 9, 14, 0, 0)

GSTT = ITrustSummary(id=uuid4(), name="Guy's & St Thomas'", code="GSTT")
KCH = ITrustSummary(id=uuid4(), name="King's College Hospital", code="KCH")
ROSTER = [GSTT, KCH]


def _round_started(round_no: int, at: datetime, total: int = 5) -> FLLogs:
    return FLLogs(
        model_id=MODEL_ID,
        success=True,
        log_date=at,
        event_type=FLLogEvent.ROUND_STARTED.value,
        global_round=round_no,
        details={"total_rounds": total},
    )


def _client_result(trust_id, round_no: int, at: datetime) -> FLLogs:
    return FLLogs(
        model_id=MODEL_ID,
        success=True,
        log_date=at,
        trust=trust_id,
        event_type=FLLogEvent.CLIENT_RESULT_RECEIVED.value,
        global_round=round_no,
        details={"size_bytes": 1024},
    )


def _error(trust_id, at: datetime) -> FLLogs:
    return FLLogs(
        model_id=MODEL_ID,
        success=False,
        log_date=at,
        trust=trust_id,
        log="trust exception: boom",
    )


class TestDeriveProgress:
    def test_no_events_yields_empty_progress_with_roster(self):
        """Just-dispatched run: roster known, nothing round-shaped yet."""
        progress = derive_progress([], ROSTER, now=T0)
        assert progress.current_round is None
        assert progress.total_rounds is None
        assert progress.started_at is None
        assert [t.code for t in progress.trusts] == ["GSTT", "KCH"]
        assert all(t.last_round is None for t in progress.trusts)
        assert all(t.state == ProgressTrustState.TRAINING for t in progress.trusts)

    def test_current_and_total_come_from_round_started_events(self):
        rows = [
            _round_started(1, T0),
            _round_started(2, T0 + timedelta(minutes=8)),
        ]
        progress = derive_progress(rows, ROSTER, now=T0 + timedelta(minutes=10))
        assert progress.current_round == 2
        assert progress.total_rounds == 5
        assert progress.started_at == T0

    def test_total_follows_the_latest_round_started(self):
        """A re-run with a different round count is correct by construction."""
        rows = [
            _round_started(1, T0, total=5),
            _round_started(1, T0 + timedelta(hours=2), total=10),
        ]
        progress = derive_progress(rows, ROSTER, now=T0 + timedelta(hours=2))
        assert progress.total_rounds == 10

    def test_trust_states_returned_and_training(self):
        rows = [
            _round_started(1, T0),
            _client_result(GSTT.id, 1, T0 + timedelta(minutes=5)),
        ]
        progress = derive_progress(rows, ROSTER, now=T0 + timedelta(minutes=6))
        by_code = {t.code: t for t in progress.trusts}
        assert by_code["GSTT"].state == ProgressTrustState.RETURNED
        assert by_code["GSTT"].last_round == 1
        assert by_code["KCH"].state == ProgressTrustState.TRAINING
        assert by_code["KCH"].last_round is None

    def test_dropped_trust_has_error_and_no_later_activity(self):
        rows = [
            _round_started(1, T0),
            _client_result(GSTT.id, 1, T0 + timedelta(minutes=5)),
            _client_result(KCH.id, 1, T0 + timedelta(minutes=6)),
            _round_started(2, T0 + timedelta(minutes=7)),
            _error(KCH.id, T0 + timedelta(minutes=9)),
            _client_result(GSTT.id, 2, T0 + timedelta(minutes=12)),
        ]
        progress = derive_progress(rows, ROSTER, now=T0 + timedelta(minutes=13))
        by_code = {t.code: t for t in progress.trusts}
        assert by_code["KCH"].state == ProgressTrustState.DROPPED
        assert by_code["KCH"].last_round == 1
        assert by_code["GSTT"].state == ProgressTrustState.RETURNED

    def test_errored_trust_that_recovered_is_not_dropped(self):
        """An error followed by a later upload means the trust came back."""
        rows = [
            _round_started(1, T0),
            _error(KCH.id, T0 + timedelta(minutes=2)),
            _client_result(KCH.id, 1, T0 + timedelta(minutes=5)),
        ]
        progress = derive_progress(rows, ROSTER, now=T0 + timedelta(minutes=6))
        by_code = {t.code: t for t in progress.trusts}
        assert by_code["KCH"].state == ProgressTrustState.RETURNED

    def test_round_duration_averages_and_eta(self):
        """avg = mean gap between consecutive round starts; ETA covers the rest."""
        rows = [
            _round_started(1, T0, total=4),
            _round_started(2, T0 + timedelta(minutes=8), total=4),
            _round_started(3, T0 + timedelta(minutes=16), total=4),
        ]
        now = T0 + timedelta(minutes=18)
        progress = derive_progress(rows, ROSTER, now=now)
        assert progress.avg_round_seconds == 480.0
        # Two rounds left (3 running + 4 upcoming) at 480s each, minus the 120s
        # round 3 has already been running.
        assert progress.est_remaining_seconds == 480.0 * 2 - 120.0

    def test_eta_never_negative_and_absent_without_enough_data(self):
        rows = [_round_started(1, T0, total=1)]
        progress = derive_progress(rows, ROSTER, now=T0 + timedelta(hours=3))
        assert progress.avg_round_seconds is None
        assert progress.est_remaining_seconds is None

    def test_duplicate_events_do_not_shift_state(self):
        """Derivation is max-based: a duplicated best-effort POST changes nothing."""
        rows = [
            _round_started(1, T0),
            _round_started(1, T0),
            _client_result(GSTT.id, 1, T0 + timedelta(minutes=5)),
            _client_result(GSTT.id, 1, T0 + timedelta(minutes=5)),
        ]
        progress = derive_progress(rows, ROSTER, now=T0 + timedelta(minutes=6))
        assert progress.current_round == 1
        by_code = {t.code: t for t in progress.trusts}
        assert by_code["GSTT"].last_round == 1

    def test_per_trust_avg_round_seconds(self):
        rows = [
            _round_started(1, T0),
            _client_result(GSTT.id, 1, T0 + timedelta(minutes=6)),
            _round_started(2, T0 + timedelta(minutes=7)),
            _client_result(GSTT.id, 2, T0 + timedelta(minutes=13)),
        ]
        progress = derive_progress(rows, ROSTER, now=T0 + timedelta(minutes=14))
        by_code = {t.code: t for t in progress.trusts}
        # Gap between GSTT's consecutive uploads: 7 minutes.
        assert by_code["GSTT"].avg_round_seconds == 420.0
        assert by_code["KCH"].avg_round_seconds is None

    def test_last_event_at_tracks_the_trusts_latest_row(self):
        error_at = T0 + timedelta(minutes=9)
        rows = [
            _round_started(1, T0),
            _client_result(KCH.id, 1, T0 + timedelta(minutes=6)),
            _error(KCH.id, error_at),
        ]
        progress = derive_progress(rows, ROSTER, now=T0 + timedelta(minutes=10))
        by_code = {t.code: t for t in progress.trusts}
        assert by_code["KCH"].last_event_at == error_at
