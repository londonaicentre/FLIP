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

"""Integration coverage of role assignment / revocation against real Postgres.

Set/get/revoke operate on the ``user_role`` join table; these tests prove the
composite-PK FK constraints behave as the SQLModel definition claims, which a
mocked Session can't.
"""

from sqlmodel import col, delete, select

from flip_api.db.models.user_models import Role, RoleRef, User, UserRole


def test_assign_revoke_lists_user_roles_round_trip(session, user_factory):
    """Assign two seeded roles to a user, list them, revoke one, list again."""
    user = user_factory()
    session.add(user)
    session.commit()

    admin_role_id = session.exec(select(Role.id).where(Role.id == RoleRef.ADMIN.value)).first()
    researcher_role_id = session.exec(select(Role.id).where(Role.id == RoleRef.RESEARCHER.value)).first()
    # Seeded roles must exist; integration_engine seeds permissions/roles once per session.
    assert admin_role_id is not None
    assert researcher_role_id is not None

    session.add_all([
        UserRole(user_id=user.id, role_id=admin_role_id),
        UserRole(user_id=user.id, role_id=researcher_role_id),
    ])
    session.commit()

    after_assign = session.exec(select(UserRole.role_id).where(UserRole.user_id == user.id)).all()
    assert set(after_assign) == {admin_role_id, researcher_role_id}

    # Revoke admin
    session.execute(
        delete(UserRole).where(col(UserRole.user_id) == user.id).where(col(UserRole.role_id) == admin_role_id)
    )
    session.commit()

    after_revoke = session.exec(select(UserRole.role_id).where(UserRole.user_id == user.id)).all()
    assert set(after_revoke) == {researcher_role_id}


def test_user_role_join_returns_role_metadata(session, user_factory):
    """A simple JOIN through user_role → roles — the kind of multi-table query
    that breaks silently if a column rename misses one side."""
    user = user_factory()
    session.add(user)
    session.commit()

    session.add(UserRole(user_id=user.id, role_id=RoleRef.ADMIN.value))
    session.commit()

    row = session.exec(
        select(Role.name)
        .join(UserRole, col(UserRole.role_id) == Role.id)
        .where(UserRole.user_id == user.id)
    ).first()
    assert row == "Admin"


def test_revoke_all_roles_leaves_user_intact(session, user_factory):
    """Revoking all roles must remove join rows but leave the User row alone."""
    user = user_factory()
    session.add(user)
    session.commit()

    session.add(UserRole(user_id=user.id, role_id=RoleRef.OBSERVER.value))
    session.commit()

    session.execute(delete(UserRole).where(col(UserRole.user_id) == user.id))
    session.commit()

    assert session.exec(select(UserRole).where(UserRole.user_id == user.id)).first() is None
    assert session.get(User, user.id) is not None
