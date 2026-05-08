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
from flip_api.auth.auth_utils import has_permissions
from flip_api.db.models.user_models import PermissionRef


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


def test_has_permissions_returns_true_when_user_has_every_required_permission():
    """Happy path: every required permission resolves to a role-permission row."""
    user_id = uuid4()
    role = MagicMock(id=uuid4())
    required = [PermissionRef.CAN_CREATE_PROJECTS, PermissionRef.CAN_APPROVE_PROJECTS]

    db = MagicMock()
    db.exec.return_value.all.side_effect = [
        [role],
        [p.value for p in required],
    ]

    assert has_permissions(user_id, required, db) is True


def test_has_permissions_returns_false_when_a_required_permission_is_missing():
    """A required permission with no matching role-permission row fails the check."""
    user_id = uuid4()
    role = MagicMock(id=uuid4())

    db = MagicMock()
    db.exec.return_value.all.side_effect = [
        [role],
        [PermissionRef.CAN_CREATE_PROJECTS.value],
    ]

    assert (
        has_permissions(
            user_id,
            [PermissionRef.CAN_CREATE_PROJECTS, PermissionRef.CAN_APPROVE_PROJECTS],
            db,
        )
        is False
    )


def test_has_permissions_returns_false_when_db_raises():
    """A DB exception is logged and surfaced as a deny, not a 500."""
    db = MagicMock()
    db.exec.side_effect = RuntimeError("db down")

    assert has_permissions(uuid4(), [PermissionRef.CAN_CREATE_PROJECTS], db) is False
