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

import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException, status
from sqlmodel import delete, insert

from flip_api.db.models.user_models import PermissionRef, Role, User, UserRole, UsersAudit
from flip_api.user_services.set_user_roles import set_user_roles


@pytest.fixture
def mock_db():
    """Mock database mock_db_session fixture."""
    db = MagicMock()
    db.begin.return_value.__enter__ = MagicMock()
    db.begin.return_value.__exit__ = MagicMock()
    return db


@pytest.fixture
def user_id():
    """User ID fixture."""
    return str(uuid.uuid4())


@pytest.fixture
def mock_user(user_factory):
    """Mock user fixture."""
    return user_factory()


@pytest.fixture
def token_id(mock_db_session, user_factory):
    """Token ID fixture."""
    # Create user with ID as token ID
    # This is a placeholder, replace with actual token generation logic
    user_data = user_factory()
    token_id = user_data.id
    insert_user_query = insert(User).values(**user_data.dict())
    mock_db_session.execute(insert_user_query)
    mock_db_session.commit()
    yield str(token_id)
    # Cleanup
    print("Cleanup: Deleting user with token ID")
    user = mock_db_session.query(User).filter(User.id == token_id).first()
    mock_db_session.delete(user)
    mock_db_session.commit()


@pytest.fixture
def roles_data(roles_factory):
    """Roles data fixture."""
    return roles_factory()


@pytest.fixture
def persisted_user(mock_db_session, user_id, roles_data, user_factory, role_factory, user_role_factory):
    user_data = user_factory(id=user_id)
    insert_user_query = insert(User).values(**user_data.dict())
    mock_db_session.execute(insert_user_query)
    mock_db_session.commit()

    role_instances = [role_factory(id=role_id) for role_id in roles_data.roles]
    insert_role_query = insert(Role).values([role.dict() for role in role_instances])
    mock_db_session.execute(insert_role_query)
    mock_db_session.commit()

    user_role_instances = [user_role_factory(role_id=role_id, user_id=user_id) for role_id in roles_data.roles]
    insert_user_role_query = insert(UserRole).values([user_role.dict() for user_role in user_role_instances])
    mock_db_session.execute(insert_user_role_query)
    mock_db_session.commit()
    yield user_id, roles_data
    # Cleanup
    print("Cleanup: Deleting user and roles")
    delete_user_role_query = delete(UserRole).where(UserRole.user_id == user_id)
    mock_db_session.execute(delete_user_role_query)
    mock_db_session.commit()
    delete_roles_query = delete(Role).where(Role.id.in_(roles_data.roles))
    mock_db_session.execute(delete_roles_query)
    mock_db_session.commit()
    delete_user_audit_query = delete(UsersAudit).where(UsersAudit.user_id == user_id)
    mock_db_session.execute(delete_user_audit_query)
    mock_db_session.commit()
    delete_user_query = delete(User).where(User.id == user_id)
    mock_db_session.execute(delete_user_query)
    mock_db_session.commit()
    print("Cleanup complete.")


def test_successful_role_update(mock_db, user_id, token_id, roles_data):
    """Test successful role update."""
    # Setup: Mock two exec calls returning .all()
    mock_exec = MagicMock()
    mock_exec.side_effect = [
        MagicMock(all=MagicMock(return_value=roles_data.roles)),  # First exec: role_ids_from_db
        MagicMock(all=MagicMock(return_value=[MagicMock(id=user_id)])),  # Second exec: existing_users
    ]
    mock_db.exec = mock_exec

    # Mock delete
    mock_db.execute.return_value = MagicMock(rowcount=1)

    with patch("flip_api.user_services.set_user_roles.has_permissions") as mock_has_permissions:
        mock_has_permissions.return_value = True

        # Execute
        result = set_user_roles(user_id, roles_data, mock_db, token_id)
        print(result)

        # Assert
        assert result == roles_data
        mock_has_permissions.assert_called_once_with(token_id, [PermissionRef.CAN_MANAGE_USERS], mock_db)
        assert mock_db.exec.call_count == 2
        mock_db.execute.assert_called_once()
        mock_db.add_all.assert_called_once()
        mock_db.add.assert_called_once()  # for the audit record


def test_permission_denied(mock_db, user_id, token_id, roles_data):
    """Test when user doesn't have the required permissions."""
    with (
        patch("flip_api.user_services.set_user_roles.has_permissions") as mock_has_permissions,
        patch("flip_api.user_services.set_user_roles.logger") as mock_logger,
    ):
        mock_has_permissions.return_value = False

        # Execute and assert
        with pytest.raises(HTTPException) as exc_info:
            set_user_roles(user_id, roles_data, mock_db, token_id)

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
        mock_logger.error.assert_called_once()


def test_invalid_roles(mock_db, user_id, token_id, roles_data):
    """Test when some roles don't exist in the database."""
    # Setup
    mock_db.exec.return_value.all.return_value = []  # No roles in db

    with patch("flip_api.user_services.set_user_roles.has_permissions") as mock_has_permissions:
        mock_has_permissions.return_value = True

        # Execute and assert
        with pytest.raises(HTTPException) as exc_info:
            set_user_roles(user_id, roles_data, mock_db, token_id)

        assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
        assert "Invalid role(s):" in exc_info.value.detail
