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
"""Pure date logic of the allowance. Reservation, dedup and concurrency need Postgres: see
``tests/integration/test_cohort_draft_ledger_db_flow.py``."""

from datetime import date, datetime, timezone

import pytest

from flip_api.cohort_services.drafting.ledger import next_reset, period_start


@pytest.mark.parametrize(
    ("now", "start", "reset"),
    [
        (datetime(2026, 11, 13, 9, 0), date(2026, 11, 1), datetime(2026, 12, 1, tzinfo=timezone.utc)),
        (datetime(2026, 11, 30, 23, 59, 59), date(2026, 11, 1), datetime(2026, 12, 1, tzinfo=timezone.utc)),
        (datetime(2026, 12, 1, 0, 0), date(2026, 12, 1), datetime(2027, 1, 1, tzinfo=timezone.utc)),
        (datetime(2026, 12, 31, 23, 59), date(2026, 12, 1), datetime(2027, 1, 1, tzinfo=timezone.utc)),
    ],
)
def test_monthly_period_is_the_utc_calendar_month(now, start, reset):
    assert period_start(now) == start
    assert next_reset(now) == reset
