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

from datetime import datetime

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from flip_api.db.models.user_models import Role, RoleRef
from flip_api.db.seed.roles import CURRENT_ROLES, seed_roles


@pytest.fixture
def session():
    """An isolated in-memory SQLite DB with only the ``roles`` table created.

    Real (not mocked) so the primary-key and unique constraints are enforced —
    that is what the rename regression below depends on.
    """
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine, tables=[Role.__table__])
    with Session(engine) as session:
        yield session


def test_seed_roles_seeds_all_on_empty_db(session):
    """Every predefined role is inserted into an empty database."""
    seed_roles(session)

    by_id = {r.id: r.name for r in session.exec(select(Role)).all()}
    assert by_id == {r["id"]: r["name"] for r in CURRENT_ROLES}


def test_seed_roles_is_idempotent(session):
    """Re-running the seed does not raise or create duplicates."""
    seed_roles(session)
    seed_roles(session)

    assert len(session.exec(select(Role)).all()) == 3


def test_seed_roles_applies_rename_keeping_same_id(session):
    """Regression: a role renamed while keeping its id (observer → viewer) must
    update the existing row in place, not insert a duplicate id.

    The live hub DB holds id ``cdee79c9-…`` under its old name ``Observer``;
    keying idempotency on the (mutable) name misses it and collides on the
    primary key when re-seeding. Keying on the id refreshes the row instead.
    """
    session.add(
        Role(
            id=RoleRef.VIEWER.value,
            name="Observer",
            description="Read-only access (pre-rename name).",
        )
    )
    session.commit()

    seed_roles(session)  # must not raise on the existing primary key

    roles = session.exec(select(Role)).all()
    assert len(roles) == 3  # no duplicate row for the reused id
    viewer = session.get(Role, RoleRef.VIEWER.value)
    assert viewer.name == "Viewer"  # rename applied in place


def test_seed_roles_bumps_updated_at_on_rename(session):
    """A rename refreshes ``updated_at`` so it reflects the modification, not creation."""
    past = datetime(2000, 1, 1)
    session.add(
        Role(
            id=RoleRef.VIEWER.value,
            name="Observer",
            description="Read-only access (pre-rename name).",
            created_at=past,
            updated_at=past,
        )
    )
    session.commit()

    seed_roles(session)

    viewer = session.get(Role, RoleRef.VIEWER.value)
    assert viewer.updated_at > past  # modification time moved forward


def test_seed_roles_unchanged_reseed_leaves_updated_at(session):
    """An ordinary re-seed with nothing changed must not bump ``updated_at``."""
    seed_roles(session)
    before = session.get(Role, RoleRef.VIEWER.value).updated_at

    seed_roles(session)

    assert session.get(Role, RoleRef.VIEWER.value).updated_at == before
