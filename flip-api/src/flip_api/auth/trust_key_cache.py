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

"""In-process TTL cache for trust API-key → trust_id lookups.

`authenticate_trust` (auth/access_manager.py) defends against timing oracles
by sweeping every hash-bearing trust row with `hmac.compare_digest` on each
request. With 30/min `/tasks/pending` plus 30/min `/trust/heartbeat` per
trust, that is an O(N) DB walk on every request in steady state — and the
sweep duration leaks the trust count to a pre-auth probe.

This cache shortens the hot path to a single primary-key fetch + one
constant-time compare. Verification still runs against the live row, so a
deleted or soft-disabled trust falls through to the full sweep (which
filters them out) and returns 401 normally; the cache cannot grant access
that the database denies.

Invalidation: callers that commit a write to the `trust` table
(`register_trust`, soft-disable when wired up, `delete_trust`) should call
`invalidate()` after commit. Cross-process eviction is not provided — the
60-second TTL bounds the staleness window across Fargate tasks. The
verify-against-the-live-row step is what makes this safe: stale entries
cost an extra DB round-trip on the request that races a write, never an
authentication bypass.
"""

import threading
import time
from uuid import UUID

_TTL_SECONDS = 60

_lock = threading.Lock()
_cache: dict[str, tuple[UUID, float]] = {}


def lookup(api_key_hash: str) -> UUID | None:
    """Return the cached trust id for this hash, or None if absent or expired.

    Args:
        api_key_hash (str): SHA-256 hex digest of the candidate API key.

    Returns:
        UUID | None: Trust id last seen for this hash, or None.
    """
    with _lock:
        entry = _cache.get(api_key_hash)
        if entry is None:
            return None
        trust_id, expires_at = entry
        if time.monotonic() >= expires_at:
            del _cache[api_key_hash]
            return None
        return trust_id


def remember(api_key_hash: str, trust_id: UUID) -> None:
    """Record that this hash resolved to this trust id; entry lasts for the TTL.

    Args:
        api_key_hash (str): SHA-256 hex digest of the API key the request carried.
        trust_id (UUID): Primary key of the matching trust row.
    """
    expires_at = time.monotonic() + _TTL_SECONDS
    with _lock:
        _cache[api_key_hash] = (trust_id, expires_at)


def invalidate() -> None:
    """Drop every cache entry. Call after register/disable/delete commits."""
    with _lock:
        _cache.clear()
