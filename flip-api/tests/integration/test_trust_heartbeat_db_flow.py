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

"""Integration coverage of the trust heartbeat against real Postgres.

A trust registered with an arbitrary display name (spaces + parentheses)
authenticates by its API key alone. ``POST /api/trust/heartbeat`` returns the
resolved ``{trust_id, trust_name}`` (so the trust-api can self-check) and
stamps the ``last_heartbeat`` column. Exercises the full chain — DB row →
``authenticate_trust`` → handler — through a real session, which mocked unit
tests cannot.
"""

import hashlib
from uuid import UUID

from sqlmodel import select

from flip_api.config import get_settings
from flip_api.db.models.main_models import Trust


def test_trust_with_spaced_name_authenticates_and_heartbeats(client, session):
    """A trust whose name contains spaces and parens heartbeats successfully."""
    api_key = "integration-test-trust-key"
    trust = Trust(
        name="(Mock) Guys and St Thomas NHS Trust",
        api_key_hash=hashlib.sha256(api_key.encode()).hexdigest(),
    )
    session.add(trust)
    session.commit()
    session.refresh(trust)
    trust_id = trust.id

    header = get_settings().TRUST_API_KEY_HEADER
    response = client.post("/api/trust/heartbeat", headers={header: api_key})

    assert response.status_code == 200, response.text
    body = response.json()
    # Identity comes back in the response — the trust-api uses this for its
    # self-check against an EXPECTED_TRUST_ID in its kit file.
    assert UUID(body["trust_id"]) == trust_id
    assert body["trust_name"] == "(Mock) Guys and St Thomas NHS Trust"
    assert body["message"] == "Heartbeat recorded"

    # The endpoint commits on its own request-scoped session; expire this test
    # session's identity map so the re-read reflects that committed write.
    session.expire_all()
    refreshed = session.exec(select(Trust).where(Trust.id == trust_id)).first()
    assert refreshed is not None
    assert refreshed.last_heartbeat is not None


def test_invalid_api_key_returns_401(client):
    """An unknown key never resolves to a row → 401, no leak of registered trust names."""
    header = get_settings().TRUST_API_KEY_HEADER
    response = client.post("/api/trust/heartbeat", headers={header: "definitely-not-a-real-key"})

    assert response.status_code == 401
