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

from flip_api.auth import trust_key_cache


@pytest.fixture(autouse=True)
def _reset_cache():
    trust_key_cache.invalidate()
    yield
    trust_key_cache.invalidate()


def test_lookup_returns_none_for_unknown_hash():
    assert trust_key_cache.lookup("unknown") is None


def test_remember_then_lookup_returns_trust_id():
    trust_id = uuid4()
    trust_key_cache.remember("h1", trust_id)
    assert trust_key_cache.lookup("h1") == trust_id


def test_entry_expires_after_ttl(monkeypatch):
    """The cache uses ``time.monotonic`` for expiry — advance the clock past TTL
    and the next lookup must drop the entry.
    """
    now = [1_000.0]
    monkeypatch.setattr(trust_key_cache.time, "monotonic", lambda: now[0])

    trust_key_cache.remember("h1", uuid4())
    assert trust_key_cache.lookup("h1") is not None

    now[0] += trust_key_cache._TTL_SECONDS + 1
    assert trust_key_cache.lookup("h1") is None


def test_invalidate_drops_every_entry():
    trust_key_cache.remember("h1", uuid4())
    trust_key_cache.remember("h2", uuid4())

    trust_key_cache.invalidate()

    assert trust_key_cache.lookup("h1") is None
    assert trust_key_cache.lookup("h2") is None


def test_remember_overwrites_previous_entry():
    """Re-registering the same hash (e.g. after a key rotation) replaces, not stacks."""
    first_id = uuid4()
    second_id = uuid4()
    trust_key_cache.remember("h1", first_id)
    trust_key_cache.remember("h1", second_id)
    assert trust_key_cache.lookup("h1") == second_id
