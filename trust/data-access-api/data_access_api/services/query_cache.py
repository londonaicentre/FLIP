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

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd

from data_access_api.config import get_settings
from data_access_api.utils.logger import logger


@dataclass
class CacheEntry:
    df: pd.DataFrame
    created_at: datetime


_cache: dict[str, CacheEntry] = {}


def _make_cache_key(query: Any, params: Mapping[str, Any] | None = None) -> str:
    query_str = query if isinstance(query, str) else str(query)
    normalized = " ".join(query_str.strip().lower().split())
    if params:
        normalized_params = {
            k: sorted(v) if isinstance(v, (list, tuple)) else v
            for k, v in sorted(params.items())
        }
        normalized += "|" + repr(normalized_params)
    return hashlib.sha256(normalized.encode()).hexdigest()


def get_cached_result(query: Any, params: Mapping[str, Any] | None = None) -> pd.DataFrame | None:
    key = _make_cache_key(query, params)
    entry = _cache.get(key)
    if entry is None:
        return None

    ttl_days = get_settings().CACHE_TTL_DAYS
    if datetime.now(UTC) - entry.created_at > timedelta(days=ttl_days):
        logger.info(f"Cache entry expired for query hash {key[:12]}...")
        del _cache[key]
        return None

    logger.info(f"Cache hit for query hash {key[:12]}...")
    return entry.df.copy()


def set_cached_result(query: Any, df: pd.DataFrame, params: Mapping[str, Any] | None = None) -> None:
    max_rows = get_settings().CACHE_MAX_RESULT_ROWS
    max_entries = get_settings().CACHE_MAX_ENTRIES

    key = _make_cache_key(query, params)

    # Skip caching results that are too large to keep memory usage bounded
    if len(df) > max_rows:
        logger.info(f"Skipping cache for query hash {key[:12]}...: {len(df)} rows exceeds limit of {max_rows}")
        return

    # Evict oldest entry when cache is full to bound total memory usage
    if len(_cache) >= max_entries and key not in _cache:
        oldest_key = min(_cache, key=lambda k: _cache[k].created_at)
        del _cache[oldest_key]
    _cache[key] = CacheEntry(df=df.copy(), created_at=datetime.now(UTC))
    logger.info(f"Cached result for query hash {key[:12]}... ({len(_cache)} entries in cache)")


def clear_cache() -> None:
    _cache.clear()
    logger.info("Query cache cleared")
