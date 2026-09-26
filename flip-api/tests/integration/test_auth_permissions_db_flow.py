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
404 users with no roles. ``has_trust_permissions`` (FLIP#1260) answers trust-scoped
authority through the same join family with ``user_role.trust_id`` pinned.

Mocked-Session unit tests can't catch a join-column rename, an FK drift, or the seed
contract drifting away from ``PermissionRef`` / ``RoleRef`` — these can.
"""

from uuid import uuid4

from flip_api.auth.auth_utils import has_permissions, has_trust_permissions
from flip_api.db.models.main_models import Trust
from flip_api.db.models.user_models import (
    TRUST_SCOPED_PERMISSIONS,
    PermissionRef,
    RolePermission,
    RoleRef,
    UserRole,
)
from flip_api.user_services.retrieve_user_permissions import get_user_permissions, has_role


def test_has_permissions_true_when_admin_holds_every_global_permission(session):
    """An Admin user passes every GLOBAL ``PermissionRef`` check — Admin is seeded with all of them.

    Trust-scoped permissions are the deliberate exception (FLIP#1260): they are
    withheld from Admin, so asking for the full enum — which includes them — must fail.
    """
    user_id = uuid4()
    session.add(UserRole(user_id=user_id, role_id=RoleRef.ADMIN.value))
    session.commit()

    global_permissions = [p for p in PermissionRef if p.value not in TRUST_SCOPED_PERMISSIONS]
    # All of them, in one call — proves the ALL-required semantics, not just any-one.
    assert has_permissions(user_id, global_permissions, session) is True

    # The full enum now includes the trust-scoped permissions Admin must not hold.
    assert has_permissions(user_id, list(PermissionRef), session) is False


def test_has_permissions_false_when_admin_requests_trust_scoped_permission(session):
    """Each trust-scoped permission is denied for Admin — the FLIP#1260 invariant.

    Hub-wide administration is not trust authority: if the seeder ever leaks one of
    these onto Admin, every hub admin gains approval rights over every trust — the
    hole #1258 exists to close.
    """
    user_id = uuid4()
    session.add(UserRole(user_id=user_id, role_id=RoleRef.ADMIN.value))
    session.commit()

    trust_scoped = [p for p in PermissionRef if p.value in TRUST_SCOPED_PERMISSIONS]
    assert trust_scoped, "TRUST_SCOPED_PERMISSIONS must reference PermissionRef members"
    for permission in trust_scoped:
        assert has_permissions(user_id, [permission], session) is False


def test_has_trust_permissions_true_only_at_the_granted_trust(session):
    """A trust-scoped Trust Owner grant answers at that trust and nowhere else (FLIP#1260)."""
    user_id = uuid4()
    trust = Trust(name="Trust Owner Flow A")
    session.add(trust)
    session.flush()  # allocate trust.id before the FK row references it
    session.add(UserRole(user_id=user_id, role_id=RoleRef.TRUST_OWNER.value, trust_id=trust.id))
    session.commit()

    assert has_trust_permissions(user_id, [PermissionRef.CAN_APPROVE_FOR_TRUST], trust.id, session) is True

    other_trust = Trust(name="Trust Owner Flow B")
    session.add(other_trust)
    session.flush()
    assert (
        has_trust_permissions(user_id, [PermissionRef.CAN_APPROVE_FOR_TRUST], other_trust.id, session)
        is False
    )


def test_has_trust_permissions_ignores_global_admin_grant(session):
    """A platform-wide Admin grant must NOT satisfy a trust-scoped check (FLIP#1258, #1260).

    The seeder grants Admin every global permission; if this check ever considered
    ``trust_id IS NULL`` rows, every hub admin would hold approval authority over
    every trust. The unit suite pins the logic with mocks — this pins it against
    the real joins.
    """
    user_id = uuid4()
    session.add(UserRole(user_id=user_id, role_id=RoleRef.ADMIN.value))
    trust = Trust(name="Unrelated Trust")
    session.add(trust)
    session.commit()

    assert has_trust_permissions(user_id, [PermissionRef.CAN_APPROVE_FOR_TRUST], trust.id, session) is False


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


def test_has_permissions_returns_false_for_viewer_with_no_seeded_perms(session):
    """Viewer is seeded with zero RolePermission rows; any permission check must fail."""
    user_id = uuid4()
    session.add(UserRole(user_id=user_id, role_id=RoleRef.VIEWER.value))
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
    """Admin + Researcher in combination must dedupe — CAN_CREATE_PROJECTS appears once, not twice.

    Admin's set is every GLOBAL permission; the trust-scoped ones are withheld (FLIP#1260).
    """
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
    expected = {p.name for p in PermissionRef if p.value not in TRUST_SCOPED_PERMISSIONS}
    assert set(names) == expected


def test_get_user_permissions_returns_empty_for_user_without_roles(session):
    """A user_id with no UserRole rows yields an empty list — the upstream 404 path."""
    user_id = uuid4()

    assert get_user_permissions(user_id, session) == []


def test_has_role_true_when_user_has_any_role(session):
    user_id = uuid4()
    session.add(UserRole(user_id=user_id, role_id=RoleRef.VIEWER.value))
    session.commit()

    assert has_role(user_id, session) is True


def test_has_role_false_when_user_has_no_roles(session):
    """Distinguishes "user exists, no role" (401/404 path) from "user has perms" (allowed)."""
    user_id = uuid4()

    assert has_role(user_id, session) is False


def test_role_permission_seed_contract(session):
    """Full seed-contract check across every role in ``RoleRef``.

    Four failure modes are caught here:

    * a new ``PermissionRef`` value is added but ``seed_role_permissions`` is not updated to
      grant it to Admin (Admin auth on that perm silently fails in prod);
    * a perm is pulled from a role's grant list (the role loses access without anyone noticing);
    * a perm leaks onto Viewer (read-only role escalates);
    * a TRUST-SCOPED perm leaks onto Admin (FLIP#1260: hub-wide admin is not trust authority —
      every hub admin would gain approval rights over every trust).

    Expected map mirrors the docstring on ``seed_role_permissions``. If you change the seed,
    update this map in the same commit — that's the point of the contract.
    """
    expected_by_role = {
        RoleRef.ADMIN.value: {p.value for p in PermissionRef if p.value not in TRUST_SCOPED_PERMISSIONS},
        RoleRef.RESEARCHER.value: {PermissionRef.CAN_CREATE_PROJECTS.value},
        RoleRef.VIEWER.value: set(),
        RoleRef.TRUST_OWNER.value: set(TRUST_SCOPED_PERMISSIONS),
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
