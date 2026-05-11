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

"""Integration coverage of permission resolution against real Postgres.

``has_permissions`` is the gatekeeper for every authenticated route on the Hub: it walks
``user_role → role_permission → permission`` to decide whether to admit a request — keyed on
the Cognito ``sub`` (Cognito is the source of truth for user identity; there is no local
users table). ``get_user_permissions``
traverses the same join family (read-only, hydrating the Permission rows themselves) and
feeds ``GET /users/{id}/permissions``. ``has_role`` is the smaller existence check used to
404 users with no roles.

Mocked-Session unit tests can't catch a join-column rename, an FK drift, or the seed
contract drifting away from ``PermissionRef`` / ``RoleRef`` — these can.
"""

from uuid import uuid4

from flip_api.auth.auth_utils import has_permissions
from flip_api.db.models.user_models import (
    PermissionRef,
    RolePermission,
    RoleRef,
    UserRole,
)
from flip_api.user_services.retrieve_user_permissions import get_user_permissions, has_role


def test_has_permissions_returns_true_when_admin_role_grants_every_permission(session):
    """An Admin user should pass every ``PermissionRef`` check — Admin is seeded with all of them."""
    user_id = uuid4()
    session.add(UserRole(user_id=user_id, role_id=RoleRef.ADMIN.value))
    session.commit()

    # All permissions, in one call — proves the ALL-required semantics, not just any-one.
    assert has_permissions(user_id, list(PermissionRef), session) is True


def test_has_permissions_returns_false_when_researcher_lacks_admin_only_permission(session):
    """A Researcher has CAN_CREATE_PROJECTS but not CAN_APPROVE_PROJECTS — the deny path."""
    user_id = uuid4()
    session.add(UserRole(user_id=user_id, role_id=RoleRef.RESEARCHER.value))
    session.commit()

    assert has_permissions(user_id, [PermissionRef.CAN_CREATE_PROJECTS], session) is True
    assert has_permissions(user_id, [PermissionRef.CAN_APPROVE_PROJECTS], session) is False
    # Mixed list must short-circuit to False — a single missing perm denies the whole check.
    assert (
        has_permissions(
            user_id, [PermissionRef.CAN_CREATE_PROJECTS, PermissionRef.CAN_APPROVE_PROJECTS], session
        )
        is False
    )


def test_has_permissions_returns_false_for_observer_with_no_seeded_perms(session):
    """Observer is seeded with zero RolePermission rows; any permission check must fail."""
    user_id = uuid4()
    session.add(UserRole(user_id=user_id, role_id=RoleRef.OBSERVER.value))
    session.commit()

    assert has_permissions(user_id, [PermissionRef.CAN_CREATE_PROJECTS], session) is False


def test_has_permissions_dedupes_across_multiple_roles(session):
    """A user with both Admin and Researcher should still pass — overlapping perms must not break the check."""
    user_id = uuid4()
    session.add_all(
        [
            UserRole(user_id=user_id, role_id=RoleRef.ADMIN.value),
            UserRole(user_id=user_id, role_id=RoleRef.RESEARCHER.value),
        ]
    )
    session.commit()

    assert has_permissions(user_id, [PermissionRef.CAN_CREATE_PROJECTS], session) is True


def test_has_permissions_returns_false_for_unknown_user(session):
    """An unknown user_id has no UserRole rows; the check must short-circuit to False, not raise."""
    assert has_permissions(uuid4(), [PermissionRef.CAN_CREATE_PROJECTS], session) is False


def test_get_user_permissions_returns_permission_rows_for_researcher(session):
    """``get_user_permissions`` hydrates the Permission rows themselves, not just IDs."""
    user_id = uuid4()
    session.add(UserRole(user_id=user_id, role_id=RoleRef.RESEARCHER.value))
    session.commit()

    perms = get_user_permissions(user_id, session)

    assert {p.permission_name for p in perms} == {PermissionRef.CAN_CREATE_PROJECTS.name}


def test_get_user_permissions_returns_admin_permissions_deduped(session):
    """Admin + Researcher in combination must dedupe — CAN_CREATE_PROJECTS appears once, not twice."""
    user_id = uuid4()
    session.add_all(
        [
            UserRole(user_id=user_id, role_id=RoleRef.ADMIN.value),
            UserRole(user_id=user_id, role_id=RoleRef.RESEARCHER.value),
        ]
    )
    session.commit()

    perms = get_user_permissions(user_id, session)
    names = [p.permission_name for p in perms]

    assert len(names) == len(set(names)), "Duplicates leak when role permissions overlap"
    assert set(names) == {p.name for p in PermissionRef}


def test_get_user_permissions_returns_empty_for_user_without_roles(session):
    """A user_id with no UserRole rows yields an empty list — the upstream 404 path."""
    user_id = uuid4()

    assert get_user_permissions(user_id, session) == []


def test_has_role_true_when_user_has_any_role(session):
    user_id = uuid4()
    session.add(UserRole(user_id=user_id, role_id=RoleRef.OBSERVER.value))
    session.commit()

    assert has_role(user_id, session) is True


def test_has_role_false_when_user_has_no_roles(session):
    """Distinguishes "user exists, no role" (401/404 path) from "user has perms" (allowed)."""
    user_id = uuid4()

    assert has_role(user_id, session) is False


def test_role_permission_seed_contract(session):
    """Full seed-contract check across every role in ``RoleRef``.

    Three failure modes are caught here:

    * a new ``PermissionRef`` value is added but ``seed_role_permissions`` is not updated to
      grant it to Admin (Admin auth on that perm silently fails in prod);
    * a perm is pulled from a role's grant list (the role loses access without anyone noticing);
    * a perm leaks onto Observer (read-only role escalates).

    Expected map mirrors the docstring on ``seed_role_permissions``. If you change the seed,
    update this map in the same commit — that's the point of the contract.
    """
    expected_by_role = {
        RoleRef.ADMIN.value: {p.value for p in PermissionRef},
        RoleRef.RESEARCHER.value: {PermissionRef.CAN_CREATE_PROJECTS.value},
        RoleRef.OBSERVER.value: set(),
    }

    rows = session.exec(RolePermission.__table__.select()).all()
    granted_by_role: dict = {}
    for row in rows:
        granted_by_role.setdefault(row.role_id, set()).add(row.permission_id)

    for role_id, expected in expected_by_role.items():
        granted = granted_by_role.get(role_id, set())
        assert granted == expected, (
            f"role-perm seed drift for role {role_id}: "
            f"missing {expected - granted}, extra {granted - expected}"
        )
