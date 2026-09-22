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

import pytest
from sqlmodel import Session

from flip_api.db.models.user_models import TRUST_SCOPED_PERMISSIONS, PermissionRef, RolePermission
from flip_api.db.seed.role_permissions import _grant_permissions, seed_role_permissions


@pytest.fixture
def mock_session():
    """Create a mock database session."""
    return MagicMock(spec=Session)


def _exec_result(first=None, all_=None):
    """Build a MagicMock matching session.exec().first() / .all() shape."""
    result = MagicMock()
    result.first = MagicMock(return_value=first)
    result.all = MagicMock(return_value=all_ if all_ is not None else [])
    return result


def test_grant_permissions_inserts_missing_pairs(mock_session):
    """_grant_permissions adds RolePermission rows for permissions not already present."""
    role_id = uuid4()
    perm_ids = [uuid4(), uuid4()]

    mock_session.exec.side_effect = [_exec_result(first=None), _exec_result(first=None)]

    _grant_permissions(mock_session, role_id, perm_ids)

    assert mock_session.add.call_count == 2
    added = [c.args[0] for c in mock_session.add.call_args_list]
    assert all(isinstance(rp, RolePermission) for rp in added)
    assert {rp.permission_id for rp in added} == set(perm_ids)
    assert all(rp.role_id == role_id for rp in added)
    mock_session.commit.assert_called_once()


def test_grant_permissions_skips_existing_pairs(mock_session):
    """Existing (role_id, permission_id) pairs must be skipped — idempotency."""
    role_id = uuid4()
    perm_ids = [uuid4(), uuid4()]

    existing_row = MagicMock(spec=RolePermission)
    mock_session.exec.side_effect = [_exec_result(first=existing_row), _exec_result(first=None)]

    _grant_permissions(mock_session, role_id, perm_ids)

    assert mock_session.add.call_count == 1
    added = mock_session.add.call_args_list[0].args[0]
    assert added.permission_id == perm_ids[1]
    mock_session.commit.assert_called_once()


def test_grant_permissions_with_empty_list_still_commits(mock_session):
    """Empty permission list short-circuits the loop but commits once."""
    _grant_permissions(mock_session, uuid4(), [])

    mock_session.add.assert_not_called()
    mock_session.commit.assert_called_once()


def test_seed_role_permissions_grants_admin_all_and_researcher_create_only(mock_session):
    """Admin gets every permission; Researcher gets CAN_CREATE_PROJECTS only (see issue #358)."""
    admin_role_id = uuid4()
    researcher_role_id = uuid4()
    admin_perms = [MagicMock(id=uuid4()), MagicMock(id=uuid4()), MagicMock(id=uuid4())]

    mock_session.exec.side_effect = [
        _exec_result(first=admin_role_id),
        _exec_result(all_=admin_perms),
        _exec_result(first=None),
        _exec_result(first=None),
        _exec_result(first=None),
        _exec_result(first=researcher_role_id),
        _exec_result(first=None),
        _exec_result(first=None),
    ]

    seed_role_permissions(mock_session)

    added = [c.args[0] for c in mock_session.add.call_args_list]
    assert len(added) == len(admin_perms) + 1

    admin_added = [rp for rp in added if rp.role_id == admin_role_id]
    assert len(admin_added) == len(admin_perms)
    assert {rp.permission_id for rp in admin_added} == {p.id for p in admin_perms}

    researcher_added = [rp for rp in added if rp.role_id == researcher_role_id]
    assert len(researcher_added) == 1
    assert researcher_added[0].permission_id == PermissionRef.CAN_CREATE_PROJECTS.value
    assert PermissionRef.CAN_MANAGE_PROJECTS.value not in {rp.permission_id for rp in researcher_added}


def test_seed_role_permissions_withholds_trust_scoped_permissions_from_admin(mock_session):
    """Admin must NOT receive trust-scoped permissions (FLIP#1260).

    The Admin grant is a blanket "every permission in the table", so a trust-scoped
    permission added to the enum would otherwise be handed to every hub administrator —
    giving them approval authority over every trust's data, the hole FLIP#1258 closes.
    This pins the filter that keeps them out.
    """
    admin_role_id = uuid4()
    ordinary = MagicMock(id=PermissionRef.CAN_CREATE_PROJECTS.value)
    trust_scoped = [MagicMock(id=pid) for pid in sorted(TRUST_SCOPED_PERMISSIONS)]

    mock_session.exec.side_effect = [
        _exec_result(first=admin_role_id),
        _exec_result(all_=[ordinary, *trust_scoped]),
        _exec_result(first=None),
        _exec_result(first=None),
        _exec_result(first=None),
        _exec_result(first=None),
    ]

    seed_role_permissions(mock_session)

    granted = {c.args[0].permission_id for c in mock_session.add.call_args_list}
    assert granted == {PermissionRef.CAN_CREATE_PROJECTS.value}
    assert not (granted & TRUST_SCOPED_PERMISSIONS)


def test_seed_role_permissions_grants_trust_scoped_permissions_to_trust_owner(mock_session):
    """Trust Owner receives exactly the trust-scoped permissions.

    The grant is global in ``role_permission`` (that table has no trust dimension); what
    binds the authority to one trust is ``user_role.trust_id`` on the user's own grant.
    """
    trust_owner_role_id = uuid4()

    mock_session.exec.side_effect = [
        _exec_result(first=None),
        _exec_result(first=None),
        _exec_result(first=trust_owner_role_id),
        *[_exec_result(first=None) for _ in TRUST_SCOPED_PERMISSIONS],
    ]

    seed_role_permissions(mock_session)

    added = [c.args[0] for c in mock_session.add.call_args_list]
    assert {rp.permission_id for rp in added} == set(TRUST_SCOPED_PERMISSIONS)
    assert all(rp.role_id == trust_owner_role_id for rp in added)


def test_seed_role_permissions_logs_when_admin_role_missing(mock_session, caplog):
    """Missing Admin role logs a debug message and skips the admin grant."""
    researcher_role_id = uuid4()

    mock_session.exec.side_effect = [
        _exec_result(first=None),
        _exec_result(first=researcher_role_id),
        _exec_result(first=None),
        _exec_result(first=None),
    ]

    with caplog.at_level("DEBUG", logger="uvicorn"):
        seed_role_permissions(mock_session)

    assert any("Admin role not found" in rec.message for rec in caplog.records)
    added = [c.args[0] for c in mock_session.add.call_args_list]
    assert len(added) == 1
    assert added[0].role_id == researcher_role_id


def test_seed_role_permissions_logs_when_researcher_role_missing(mock_session, caplog):
    """Missing Researcher role logs a debug message and skips the researcher grant."""
    admin_role_id = uuid4()

    mock_session.exec.side_effect = [
        _exec_result(first=admin_role_id),
        _exec_result(all_=[]),
        _exec_result(first=None),
        _exec_result(first=None),
    ]

    with caplog.at_level("DEBUG", logger="uvicorn"):
        seed_role_permissions(mock_session)

    assert any("Researcher role not found" in rec.message for rec in caplog.records)
    mock_session.add.assert_not_called()


def test_seed_role_permissions_no_roles_still_logs_completion(mock_session, caplog):
    """With neither Admin nor Researcher roles seeded, the function logs both
    debug warnings and still emits the final info message."""
    mock_session.exec.side_effect = [
        _exec_result(first=None),
        _exec_result(first=None),
        _exec_result(first=None),
    ]

    with caplog.at_level("DEBUG", logger="uvicorn"):
        seed_role_permissions(mock_session)

    messages = [rec.message for rec in caplog.records]
    assert any("Admin role not found" in m for m in messages)
    assert any("Researcher role not found" in m for m in messages)
    assert any("Trust Owner role not found" in m for m in messages)
    assert any("Role permissions seeded successfully." in m for m in messages)
    mock_session.add.assert_not_called()
