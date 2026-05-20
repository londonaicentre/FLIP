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

A trust registered with an arbitrary display name (spaces + parentheses) must
authenticate by its API key and heartbeat at ``POST /trust/{name}/heartbeat``. This
exercises the full chain — ``_collect_trust_hashes`` → ``authenticate_trust`` →
``verify_trust_identity`` → ``_get_trust_by_name`` — and the URL-path encoding of a
name with spaces and parens, which mocked unit tests cannot cover.
"""

import hashlib

from sqlmodel import select

from flip_api.config import get_settings
from flip_api.db.models.main_models import Trust


def test_trust_with_spaced_name_authenticates_and_heartbeats(client, session):
    """A trust named with spaces + parens heartbeats successfully and records last_heartbeat."""
    api_key = "integration-test-trust-key"
    trust = Trust(
        name="Open Trust (EC2)",
        api_key_hash=hashlib.sha256(api_key.encode()).hexdigest(),
    )
    session.add(trust)
    session.commit()

    header = get_settings().TRUST_API_KEY_HEADER
    response = client.post(
        "/api/trust/Open Trust (EC2)/heartbeat",
        headers={header: api_key},
    )

    assert response.status_code == 200, response.text

    refreshed = session.exec(select(Trust).where(Trust.name == "Open Trust (EC2)")).first()
    assert refreshed is not None
    assert refreshed.last_heartbeat is not None
