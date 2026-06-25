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

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from flip_api.db.models.user_models import Permission, PermissionRef
from flip_api.db.seed.permissions import seed_permissions


@pytest.fixture
def session():
    """An isolated in-memory SQLite DB with only the ``permission`` table created,
    so the primary-key constraint is enforced for the rename regression below."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine, tables=[Permission.__table__])
    with Session(engine) as session:
        yield session


def test_seed_permissions_seeds_all_on_empty_db(session):
    """Every predefined permission is inserted into an empty database."""
    seed_permissions(session)

    by_id = {p.id: p.permission_name for p in session.exec(select(Permission)).all()}
    assert by_id == {ref.value: ref.name for ref in PermissionRef}


def test_seed_permissions_is_idempotent(session):
    """Re-running the seed does not raise or create duplicates."""
    seed_permissions(session)
    seed_permissions(session)

    assert len(session.exec(select(Permission)).all()) == len(list(PermissionRef))


def test_seed_permissions_applies_rename_keeping_same_id(session):
    """Regression: a permission renamed while keeping its id must update the
    existing row in place, not insert a duplicate id (primary-key collision)."""
    ref = next(iter(PermissionRef))
    session.add(
        Permission(id=ref.value, permission_name="OLD_NAME", permission_description="OLD_NAME")
    )
    session.commit()

    seed_permissions(session)  # must not raise on the existing primary key

    assert len(session.exec(select(Permission)).all()) == len(list(PermissionRef))
    refreshed = session.get(Permission, ref.value)
    assert refreshed.permission_name == ref.name  # rename applied in place
