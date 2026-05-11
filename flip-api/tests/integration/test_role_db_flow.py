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

Set/get/revoke operate on the ``user_role`` join table, keyed on the Cognito
``sub`` UUID. Cognito is the source of truth for user identity — there is no
local users table — so tests use a bare ``uuid4()`` to stand in for a sub.
"""

from uuid import uuid4

from sqlmodel import col, delete, select

from flip_api.db.models.user_models import Role, RoleRef, UserRole


def test_assign_revoke_lists_user_roles_round_trip(session):
    """Assign two seeded roles to a user, list them, revoke one, list again."""
    user_id = uuid4()

    admin_role_id = session.exec(select(Role.id).where(Role.id == RoleRef.ADMIN.value)).first()
    researcher_role_id = session.exec(select(Role.id).where(Role.id == RoleRef.RESEARCHER.value)).first()
    # Seeded roles must exist; integration_engine seeds permissions/roles once per session.
    assert admin_role_id is not None
    assert researcher_role_id is not None

    session.add_all([
        UserRole(user_id=user_id, role_id=admin_role_id),
        UserRole(user_id=user_id, role_id=researcher_role_id),
    ])
    session.commit()

    after_assign = session.exec(select(UserRole.role_id).where(UserRole.user_id == user_id)).all()
    assert set(after_assign) == {admin_role_id, researcher_role_id}

    # Revoke admin
    session.execute(
        delete(UserRole).where(col(UserRole.user_id) == user_id).where(col(UserRole.role_id) == admin_role_id)
    )
    session.commit()

    after_revoke = session.exec(select(UserRole.role_id).where(UserRole.user_id == user_id)).all()
    assert set(after_revoke) == {researcher_role_id}


def test_user_role_join_returns_role_metadata(session):
    """A simple JOIN through user_role → roles — the kind of multi-table query
    that breaks silently if a column rename misses one side."""
    user_id = uuid4()

    session.add(UserRole(user_id=user_id, role_id=RoleRef.ADMIN.value))
    session.commit()

    row = session.exec(
        select(Role.name)
        .join(UserRole, col(UserRole.role_id) == Role.id)
        .where(UserRole.user_id == user_id)
    ).first()
    assert row == "Admin"


def test_revoke_all_roles_leaves_no_join_rows(session):
    """Revoking all roles must remove every join row for that sub — Cognito retains the
    underlying identity, but we no longer mirror it here."""
    user_id = uuid4()

    session.add(UserRole(user_id=user_id, role_id=RoleRef.OBSERVER.value))
    session.commit()

    session.execute(delete(UserRole).where(col(UserRole.user_id) == user_id))
    session.commit()

    assert session.exec(select(UserRole).where(UserRole.user_id == user_id)).first() is None
