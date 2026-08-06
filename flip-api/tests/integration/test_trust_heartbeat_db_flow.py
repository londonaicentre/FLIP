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
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlmodel import select

from flip_api.config import get_settings
from flip_api.db.models.main_models import Trust


def _register_trust(session, name: str, api_key: str) -> UUID:
    """Insert a trust row authenticating with ``api_key`` and return its id."""
    trust = Trust(name=name, api_key_hash=hashlib.sha256(api_key.encode()).hexdigest())
    session.add(trust)
    session.commit()
    session.refresh(trust)
    return trust.id


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


def test_heartbeat_body_round_trips_jsonb_and_is_server_stamped(client, session):
    """A snapshot body survives the JSONB round-trip; services_health_at is stamped
    on receipt — the payload's own (year 2020) collected_at is never trusted."""
    api_key = "integration-test-snapshot-key"
    trust_id = _register_trust(session, "Snapshot Trust", api_key)
    services = {
        "trust-api": {"status": "healthy", "version": "0.3.0", "response_ms": None},
        "xnat": {"status": "down", "version": None, "response_ms": None},
        "omop": {"status": "degraded", "version": None, "response_ms": 1400},
    }
    before = datetime.now(timezone.utc).replace(tzinfo=None)

    header = get_settings().TRUST_API_KEY_HEADER
    response = client.post(
        "/api/trust/heartbeat",
        headers={header: api_key},
        json={"services": services, "collected_at": "2020-01-01T00:00:00+00:00"},
    )

    assert response.status_code == 200, response.text
    session.expire_all()
    refreshed = session.exec(select(Trust).where(Trust.id == trust_id)).first()
    assert refreshed is not None
    assert refreshed.services_health == services
    # The column is `timestamp without time zone` holding UTC; compare naive-UTC.
    assert refreshed.services_health_at is not None
    assert refreshed.services_health_at >= before - timedelta(seconds=1)


def test_bodyless_heartbeat_leaves_services_columns_null(client, session):
    """A pre-collector trust-api (no body) stamps last_heartbeat only."""
    api_key = "integration-test-bodyless-key"
    trust_id = _register_trust(session, "Legacy Trust", api_key)

    header = get_settings().TRUST_API_KEY_HEADER
    response = client.post("/api/trust/heartbeat", headers={header: api_key})

    assert response.status_code == 200, response.text
    session.expire_all()
    refreshed = session.exec(select(Trust).where(Trust.id == trust_id)).first()
    assert refreshed is not None
    assert refreshed.last_heartbeat is not None
    assert refreshed.services_health is None
    assert refreshed.services_health_at is None


def test_heartbeat_rejects_invalid_snapshot_and_persists_nothing(client, session):
    """An invalid snapshot 422s and neither timestamp nor services move."""
    api_key = "integration-test-invalid-key"
    trust_id = _register_trust(session, "Invalid Snapshot Trust", api_key)

    header = get_settings().TRUST_API_KEY_HEADER
    response = client.post(
        "/api/trust/heartbeat",
        headers={header: api_key},
        json={"services": {"xnat": {"status": "sideways"}}},
    )

    assert response.status_code == 422
    session.expire_all()
    refreshed = session.exec(select(Trust).where(Trust.id == trust_id)).first()
    assert refreshed is not None
    assert refreshed.last_heartbeat is None
    assert refreshed.services_health is None
