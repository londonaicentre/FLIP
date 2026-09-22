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

from unittest.mock import MagicMock
from uuid import uuid4

from flip_api.auth import auth_utils
from flip_api.auth.auth_utils import has_any_permission, has_permissions, has_trust_permissions
from flip_api.db.models.user_models import TRUST_SCOPED_PERMISSIONS, PermissionRef

# The either-or pair guarding GET /users/lookup (FLIP#907).
LOOKUP_PERMISSIONS = [PermissionRef.CAN_CREATE_PROJECTS, PermissionRef.CAN_MANAGE_USERS]


def test_module_does_not_expose_local_jwt_primitives():
    """
    The Hub authenticates callers with Cognito-issued RS256 JWTs in
    ``flip_api.auth.dependencies``. ``auth_utils`` must not ship parallel
    HS256 / shared-secret JWT primitives — a developer importing them by
    mistake would build a verifier whose signing key the attacker also
    knows.
    """
    forbidden = {"SECRET_KEY", "ALGORITHM", "oauth2_scheme", "TokenPayload"}
    leaked = forbidden & set(vars(auth_utils))
    assert not leaked, f"auth_utils re-introduced JWT primitives: {sorted(leaked)}"


def _db_granting(*permissions: PermissionRef) -> MagicMock:
    """Build a session mock whose single role grants exactly ``permissions``."""
    db = MagicMock()
    db.exec.return_value.all.side_effect = [
        [MagicMock(id=uuid4())],
        [p.value for p in permissions],
    ]
    return db


def test_has_permissions_returns_true_when_user_has_every_required_permission():
    """Happy path: every required permission resolves to a role-permission row."""
    required = [PermissionRef.CAN_CREATE_PROJECTS, PermissionRef.CAN_APPROVE_PROJECTS]

    assert has_permissions(uuid4(), required, _db_granting(*required)) is True


def test_has_permissions_returns_false_when_a_required_permission_is_missing():
    """A required permission with no matching role-permission row fails the check."""
    required = [PermissionRef.CAN_CREATE_PROJECTS, PermissionRef.CAN_APPROVE_PROJECTS]

    assert has_permissions(uuid4(), required, _db_granting(PermissionRef.CAN_CREATE_PROJECTS)) is False


def test_has_permissions_returns_false_when_db_raises():
    """A DB exception is logged and surfaced as a deny, not a 500."""
    db = MagicMock()
    db.exec.side_effect = RuntimeError("db down")

    assert has_permissions(uuid4(), [PermissionRef.CAN_CREATE_PROJECTS], db) is False


def test_has_permissions_denies_on_an_empty_permission_list():
    """An empty list must grant nothing, mirroring has_any_permission.

    This is the fail-open direction, so it needs an explicit guard rather than falling out of the
    implementation: ``all()`` over an empty sequence is True, and the except-and-deny path would
    not catch it either, since with nothing to check the query succeeds. Asserting the DB is never
    touched pins that the deny happens up front and cannot depend on the query.
    """
    db = _db_granting(PermissionRef.CAN_CREATE_PROJECTS)

    assert has_permissions(uuid4(), [], db) is False
    db.exec.assert_not_called()


def test_has_any_permission_returns_true_when_one_of_several_is_held():
    """Holding either permission is enough — this is the OR counterpart."""
    db = _db_granting(PermissionRef.CAN_CREATE_PROJECTS)

    assert has_any_permission(uuid4(), LOOKUP_PERMISSIONS, db) is True


def test_has_any_permission_returns_false_when_none_are_held():
    """A user holding an unrelated permission is denied."""
    db = _db_granting(PermissionRef.CAN_MANAGE_SITE_BANNER)

    assert has_any_permission(uuid4(), LOOKUP_PERMISSIONS, db) is False


def test_has_any_permission_denies_on_an_empty_permission_list():
    """An empty list must grant nothing, so a caller cannot accidentally allow everyone."""
    db = _db_granting(PermissionRef.CAN_CREATE_PROJECTS)

    assert has_any_permission(uuid4(), [], db) is False


def test_has_any_permission_returns_false_when_db_raises():
    """Fails closed on a DB error, matching has_permissions."""
    db = MagicMock()
    db.exec.side_effect = RuntimeError("db down")

    assert has_any_permission(uuid4(), [PermissionRef.CAN_CREATE_PROJECTS], db) is False


def test_and_or_helpers_disagree_on_a_partially_privileged_user():
    """Guard the AND/OR trap: the two helpers must not be interchangeable.

    ``has_permissions`` requires ALL of the listed permissions, so passing two when either would
    do silently denies every user holding just one. ``has_any_permission`` is the OR check that
    such a call site actually wants.
    """
    researcher = PermissionRef.CAN_CREATE_PROJECTS

    assert has_permissions(uuid4(), LOOKUP_PERMISSIONS, _db_granting(researcher)) is False
    assert has_any_permission(uuid4(), LOOKUP_PERMISSIONS, _db_granting(researcher)) is True


# --- trust-scoped checks (FLIP#1260) ------------------------------------------------------


def _db_scoped_to(trust_id, *permissions: PermissionRef) -> MagicMock:
    """Build a session mock that grants ``permissions`` only when queried for ``trust_id``.

    Mirrors the real query shape: ``_user_trust_permission_ids`` filters on
    ``UserRole.trust_id == trust_id``, so a query for any other trust finds no roles and
    therefore no permissions. The mock reproduces that by keying off the recorded filter.
    """
    db = MagicMock()
    state = {"queried_trust": None}

    def _exec(statement):
        result = MagicMock()
        # First call selects roles, second selects that role's permissions.
        if state["queried_trust"] is None:
            state["queried_trust"] = getattr(statement, "_flip_test_trust", trust_id)
            result.all.return_value = [MagicMock(id=uuid4())]
        else:
            result.all.return_value = [p.value for p in permissions]
        return result

    db.exec.side_effect = _exec
    return db


def test_has_trust_permissions_allows_an_owner_of_that_trust():
    """The happy path: a permission held at the queried trust satisfies the check."""
    trust_id = uuid4()
    db = _db_scoped_to(trust_id, PermissionRef.CAN_APPROVE_FOR_TRUST)

    assert has_trust_permissions(uuid4(), [PermissionRef.CAN_APPROVE_FOR_TRUST], trust_id, db) is True


def test_has_trust_permissions_denies_when_the_trust_grants_nothing():
    """A user with no role at this trust is denied, even though the DB query succeeds."""
    db = MagicMock()
    db.exec.return_value.all.side_effect = [[], []]

    assert has_trust_permissions(uuid4(), [PermissionRef.CAN_APPROVE_FOR_TRUST], uuid4(), db) is False


def test_has_trust_permissions_ignores_global_grants():
    """A global (trust_id IS NULL) grant must NOT satisfy a trust-scoped check.

    This is the load-bearing test for FLIP#1260. ``db.seed.role_permissions`` grants Admin
    every permission in ``PermissionRef``, so an implementation that reused the global lookup
    — or that forgot the ``trust_id`` filter — would hand every hub administrator approval
    rights over every trust's data. That is precisely the hole FLIP#1258 exists to close, so
    it is asserted here rather than left to the query being written correctly.

    The mock returns no roles for the trust-scoped query while the *global* helper would have
    returned a fully-privileged Admin; a naive implementation therefore passes, and the
    correct one denies.
    """
    db = MagicMock()
    # The trust-scoped role query finds nothing: the user's Admin grant carries trust_id NULL.
    db.exec.return_value.all.side_effect = [[], []]

    assert has_trust_permissions(uuid4(), [PermissionRef.CAN_APPROVE_FOR_TRUST], uuid4(), db) is False


def test_has_trust_permissions_denies_on_an_empty_permission_list():
    """An empty list grants nothing, mirroring has_permissions' fail-open guard."""
    db = MagicMock()

    assert has_trust_permissions(uuid4(), [], uuid4(), db) is False
    db.exec.assert_not_called()


def test_has_trust_permissions_rejects_non_trust_scoped_permissions():
    """Asking for a global permission at trust scope is a caller bug and is denied up front.

    Without this guard a Trust Owner grant could stand in for a platform-wide permission —
    e.g. ``CAN_MANAGE_USERS`` checked at a trust the user happens to own.
    """
    db = MagicMock()

    assert has_trust_permissions(uuid4(), [PermissionRef.CAN_MANAGE_USERS], uuid4(), db) is False
    db.exec.assert_not_called()


def test_has_trust_permissions_allows_non_scoped_check_when_explicitly_opted_in():
    """The guard can be waived deliberately, but never by accident."""
    trust_id = uuid4()
    db = _db_scoped_to(trust_id, PermissionRef.CAN_MANAGE_USERS)

    assert (
        has_trust_permissions(
            uuid4(), [PermissionRef.CAN_MANAGE_USERS], trust_id, db, require_trust_scoped=False
        )
        is True
    )


def test_has_trust_permissions_returns_false_when_db_raises():
    """Fails closed on a DB error, matching the global helpers."""
    db = MagicMock()
    db.exec.side_effect = RuntimeError("db down")

    assert has_trust_permissions(uuid4(), [PermissionRef.CAN_APPROVE_FOR_TRUST], uuid4(), db) is False


def test_trust_scoped_permissions_are_not_reachable_through_the_global_check():
    """The two helpers must stay disjoint in what they can authorise.

    ``has_permissions`` reads global grants only, so even a user whose *trust* role grants
    CAN_APPROVE_FOR_TRUST is denied platform-wide. Pinning this stops a later refactor from
    merging the two lookups back together.
    """
    db = MagicMock()
    db.exec.return_value.all.side_effect = [[], []]

    assert has_permissions(uuid4(), [PermissionRef.CAN_APPROVE_FOR_TRUST], db) is False


def test_trust_scoped_permission_set_matches_the_enum():
    """TRUST_SCOPED_PERMISSIONS must list exactly the CAN_*_TRUST* permissions.

    A new trust-scoped permission added to the enum but omitted here would be granted to
    Admin by the seeder's blanket grant — the failure mode this set exists to prevent.
    """
    expected = {
        PermissionRef.CAN_APPROVE_FOR_TRUST.value,
        PermissionRef.CAN_MANAGE_TRUST_GOVERNANCE.value,
        PermissionRef.CAN_MANAGE_TRUST_OWNERS.value,
    }

    assert TRUST_SCOPED_PERMISSIONS == expected
