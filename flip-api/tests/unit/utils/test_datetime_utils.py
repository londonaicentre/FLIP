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

"""Tests for the timestamp helpers (FLIP-PT-054)."""

from datetime import datetime, timedelta, timezone

from flip_api.db.models import main_models, user_models
from flip_api.utils.datetime_utils import UTC_DATETIME, utc_now


def test_utc_now_is_timezone_aware():
    """The whole point: never naive, so it can be compared with DB values."""
    assert utc_now().tzinfo is not None


def test_utc_now_is_utc():
    assert utc_now().utcoffset() == timedelta(0)


def test_utc_now_is_current():
    assert abs((utc_now() - datetime.now(timezone.utc)).total_seconds()) < 5


def test_utc_now_comparable_with_aware_datetimes():
    """Naive/aware mixing raises TypeError — guard against a regression to `utcnow`."""
    assert utc_now() >= datetime.now(timezone.utc) - timedelta(seconds=5)


def test_utc_datetime_column_type_is_timezone_aware():
    assert UTC_DATETIME.timezone is True


def test_every_model_timestamp_column_is_timezone_aware():
    """A naive TIMESTAMP column would read back naive and break comparisons."""
    naive = []
    for module in (main_models, user_models):
        for name in dir(module):
            table = getattr(getattr(module, name), "__table__", None)
            if table is None:
                continue
            for column in table.columns:
                if "TIMESTAMP" in str(column.type).upper() or "DATETIME" in str(column.type).upper():
                    if not getattr(column.type, "timezone", False):
                        naive.append(f"{table.name}.{column.name}")
    assert not naive, f"Timestamp columns must be timezone-aware: {naive}"


def test_no_model_uses_deprecated_utcnow():
    """`datetime.utcnow` is deprecated and naive — models must use `utc_now`."""
    import inspect

    for module in (main_models, user_models):
        assert "datetime.utcnow" not in inspect.getsource(module), f"{module.__name__} still uses datetime.utcnow"
