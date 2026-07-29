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

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pandas as pd
import pytest

from data_access_api.services.query_cache import (
    CacheEntry,
    _cache,
    _make_cache_key,
    clear_cache,
    get_cached_result,
    set_cached_result,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear the cache before and after each test."""
    _cache.clear()
    yield
    _cache.clear()


class TestMakeCacheKey:
    def test_deterministic(self):
        key1 = _make_cache_key("SELECT * FROM table")
        key2 = _make_cache_key("SELECT * FROM table")
        assert key1 == key2

    def test_normalizes_whitespace(self):
        key1 = _make_cache_key("SELECT  *   FROM   table")
        key2 = _make_cache_key("SELECT * FROM table")
        assert key1 == key2

    def test_case_insensitive(self):
        key1 = _make_cache_key("SELECT * FROM table")
        key2 = _make_cache_key("select * from table")
        assert key1 == key2

    def test_different_queries_different_keys(self):
        key1 = _make_cache_key("SELECT * FROM table_a")
        key2 = _make_cache_key("SELECT * FROM table_b")
        assert key1 != key2


class TestGetCachedResult:
    @patch("data_access_api.services.query_cache.get_settings")
    def test_returns_none_on_miss(self, mock_settings):
        mock_settings.return_value.CACHE_TTL_MINUTES = 15
        result = get_cached_result("SELECT 1")
        assert result is None

    @patch("data_access_api.services.query_cache.get_settings")
    def test_returns_cached_dataframe(self, mock_settings):
        mock_settings.return_value.CACHE_TTL_MINUTES = 15
        mock_settings.return_value.CACHE_MAX_RESULT_ROWS = 50_000
        mock_settings.return_value.CACHE_MAX_ENTRIES = 64
        df = pd.DataFrame({"col": [1, 2, 3]})
        set_cached_result("SELECT 1", df)

        result = get_cached_result("SELECT 1")
        assert result is not None
        pd.testing.assert_frame_equal(result, df)

    @patch("data_access_api.services.query_cache.get_settings")
    def test_returns_copy_not_reference(self, mock_settings):
        mock_settings.return_value.CACHE_TTL_MINUTES = 15
        mock_settings.return_value.CACHE_MAX_RESULT_ROWS = 50_000
        mock_settings.return_value.CACHE_MAX_ENTRIES = 64
        df = pd.DataFrame({"col": [1, 2, 3]})
        set_cached_result("SELECT 1", df)

        result = get_cached_result("SELECT 1")
        result["col"] = [99, 99, 99]

        # Original cached value should be unchanged
        result2 = get_cached_result("SELECT 1")
        pd.testing.assert_frame_equal(result2, df)

    @patch("data_access_api.services.query_cache.get_settings")
    def test_expired_entry_returns_none(self, mock_settings):
        mock_settings.return_value.CACHE_TTL_MINUTES = 15
        query = "SELECT 1"
        df = pd.DataFrame({"col": [1]})
        key = _make_cache_key(query)

        # Insert an entry that expired 1 day ago
        _cache[key] = CacheEntry(df=df.copy(), created_at=datetime.now(UTC) - timedelta(days=61))

        result = get_cached_result(query)
        assert result is None
        assert key not in _cache  # Expired entry should be removed


class TestSetCachedResult:
    @patch("data_access_api.services.query_cache.get_settings")
    def test_stores_entry(self, mock_settings):
        mock_settings.return_value.CACHE_TTL_MINUTES = 15
        mock_settings.return_value.CACHE_MAX_RESULT_ROWS = 50_000
        mock_settings.return_value.CACHE_MAX_ENTRIES = 64
        df = pd.DataFrame({"col": [1, 2]})
        set_cached_result("SELECT 1", df)
        assert len(_cache) == 1

    @patch("data_access_api.services.query_cache.get_settings")
    def test_stores_copy(self, mock_settings):
        mock_settings.return_value.CACHE_TTL_MINUTES = 15
        mock_settings.return_value.CACHE_MAX_RESULT_ROWS = 50_000
        mock_settings.return_value.CACHE_MAX_ENTRIES = 64
        df = pd.DataFrame({"col": [1, 2]})
        set_cached_result("SELECT 1", df)

        # Mutating original should not affect cache
        df["col"] = [99, 99]
        result = get_cached_result("SELECT 1")
        assert list(result["col"]) == [1, 2]

    @patch("data_access_api.services.query_cache.get_settings")
    def test_skips_caching_when_exceeding_max_rows(self, mock_settings):
        mock_settings.return_value.CACHE_TTL_MINUTES = 15
        mock_settings.return_value.CACHE_MAX_RESULT_ROWS = 5
        mock_settings.return_value.CACHE_MAX_ENTRIES = 64
        df = pd.DataFrame({"col": range(10)})  # 10 rows > limit of 5
        set_cached_result("SELECT big", df)
        assert len(_cache) == 0

    @patch("data_access_api.services.query_cache.get_settings")
    def test_caches_dataframe_within_row_limit(self, mock_settings):
        mock_settings.return_value.CACHE_TTL_MINUTES = 15
        mock_settings.return_value.CACHE_MAX_RESULT_ROWS = 10
        mock_settings.return_value.CACHE_MAX_ENTRIES = 64
        df = pd.DataFrame({"col": range(5)})  # 5 rows <= limit of 10
        set_cached_result("SELECT small", df)
        assert len(_cache) == 1

    @patch("data_access_api.services.query_cache.get_settings")
    def test_evicts_oldest_when_max_entries_reached(self, mock_settings):
        mock_settings.return_value.CACHE_TTL_MINUTES = 15
        mock_settings.return_value.CACHE_MAX_RESULT_ROWS = 50_000
        mock_settings.return_value.CACHE_MAX_ENTRIES = 2

        set_cached_result("SELECT 1", pd.DataFrame({"col": [1]}))
        set_cached_result("SELECT 2", pd.DataFrame({"col": [2]}))
        assert len(_cache) == 2

        # Adding a 3rd entry should evict the oldest (SELECT 1)
        set_cached_result("SELECT 3", pd.DataFrame({"col": [3]}))
        assert len(_cache) == 2

        key1 = _make_cache_key("SELECT 1")
        key3 = _make_cache_key("SELECT 3")
        assert key1 not in _cache  # oldest evicted
        assert key3 in _cache  # newest present

    @patch("data_access_api.services.query_cache.get_settings")
    def test_max_entries_config_value_read_from_settings(self, mock_settings):
        mock_settings.return_value.CACHE_TTL_MINUTES = 15
        mock_settings.return_value.CACHE_MAX_RESULT_ROWS = 50_000
        mock_settings.return_value.CACHE_MAX_ENTRIES = 3  # Not the old hardcoded 256

        for i in range(5):
            set_cached_result(f"SELECT {i}", pd.DataFrame({"col": [i]}))

        # Cache should never exceed configured max of 3
        assert len(_cache) == 3


class TestClearCache:
    def test_clears_all_entries(self):
        _cache["key1"] = CacheEntry(df=pd.DataFrame(), created_at=datetime.now(UTC))
        _cache["key2"] = CacheEntry(df=pd.DataFrame(), created_at=datetime.now(UTC))
        assert len(_cache) == 2

        clear_cache()
        assert len(_cache) == 0


# --- per-project scoping and TTL bounds -------------------------------------


def test_cache_is_scoped_per_project():
    """One project must never be served another project's cached rows."""
    from data_access_api.services.query_cache import clear_cache, get_cached_result, set_cached_result

    clear_cache()
    df_a = pd.DataFrame({"person_id": [1, 2, 3]})
    set_cached_result("SELECT * FROM omop.person", df_a, scope="project-a")

    assert get_cached_result("SELECT * FROM omop.person", scope="project-a") is not None
    assert get_cached_result("SELECT * FROM omop.person", scope="project-b") is None
    assert get_cached_result("SELECT * FROM omop.person") is None


def test_same_project_still_hits_cache():
    from data_access_api.services.query_cache import clear_cache, get_cached_result, set_cached_result

    clear_cache()
    df = pd.DataFrame({"person_id": [1]})
    set_cached_result("SELECT 1", df, scope="project-a")
    hit = get_cached_result("SELECT 1", scope="project-a")
    assert hit is not None
    assert len(hit) == 1


def test_entries_expire_after_ttl_minutes(monkeypatch):
    """Cohort rows are patient-derived — a stale entry must not outlive the TTL."""
    from datetime import UTC, datetime, timedelta

    from data_access_api.services import query_cache

    query_cache.clear_cache()
    query_cache.set_cached_result("SELECT 1", pd.DataFrame({"a": [1]}), scope="p")
    ttl = query_cache.get_settings().CACHE_TTL_MINUTES

    key = next(iter(query_cache._cache))
    query_cache._cache[key].created_at = datetime.now(UTC) - timedelta(minutes=ttl + 1)
    assert query_cache.get_cached_result("SELECT 1", scope="p") is None


def test_ttl_is_bounded_to_minutes_not_days():
    """Guard the governance property: the window is minutes, not months."""
    from data_access_api.config import get_settings

    assert get_settings().CACHE_TTL_MINUTES <= 60
