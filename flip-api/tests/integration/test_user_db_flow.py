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

"""Integration coverage of the User DB layer.

Cognito interaction is intentionally out of scope (B2's job, per #367). These
tests cover the slice flip-api owns: the ``users`` table — uniqueness,
roundtrip, profile updates.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from flip_api.db.models.user_models import User


def test_persist_then_fetch_by_id(session, user_factory):
    """Round-trip a User row by primary key."""
    user = user_factory(email="round.trip@example.com")
    session.add(user)
    session.commit()

    fetched = session.get(User, user.id)
    assert fetched is not None
    assert fetched.id == user.id
    assert fetched.email == "round.trip@example.com"
    assert fetched.enabled is True


def test_fetch_by_email(session, user_factory):
    """Email is uniquely indexed; fetching by it should return exactly one row."""
    user = user_factory(email="lookup.by.email@example.com")
    session.add(user)
    session.commit()

    rows = session.exec(select(User).where(User.email == "lookup.by.email@example.com")).all()
    assert len(rows) == 1
    assert rows[0].id == user.id


def test_duplicate_email_violates_unique_constraint(session, user_factory):
    """Two users with the same email must be rejected at the DB layer."""
    email = "dup@example.com"
    session.add(user_factory(email=email))
    session.commit()

    session.add(user_factory(email=email))
    with pytest.raises(IntegrityError):
        session.commit()


def test_update_profile_persists(session, user_factory):
    """Updating ``enabled``/``updated_at`` must round-trip — checks the SQL UPDATE
    actually maps to the SQLModel field."""
    user = user_factory(email="will.update@example.com", enabled=True)
    session.add(user)
    session.commit()

    later = datetime.utcnow() + timedelta(seconds=1)
    user.enabled = False
    user.updated_at = later
    session.add(user)
    session.commit()

    refreshed = session.get(User, user.id)
    assert refreshed is not None
    assert refreshed.enabled is False
    # PG truncates microseconds depending on column precision; compare with
    # tolerance rather than equality so a TIMESTAMP-vs-TIMESTAMPTZ surprise
    # doesn't make this brittle.
    assert abs((refreshed.updated_at - later).total_seconds()) < 1
