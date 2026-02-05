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
from uuid import UUID

import pytest
from sqlmodel import delete, insert

from flip_api.db.models.user_models import Role, User, UserRole, UsersAudit
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


@pytest.mark.skip
def test_audit_record_creation(mock_db, user_id, token_id, roles_data):
    """Test the audit record creation."""
    # Setup
    role_instances = [MagicMock(role_id=role_id) for role_id in roles_data.roles]
    mock_db.exec.return_value.all.return_value = role_instances

    with (
        patch("flip_api.user_services.set_user_roles.has_permissions") as mock_has_permissions,
        patch("flip_api.user_services.set_user_roles.UsersAudit") as mock_audit_class,
    ):
        mock_has_permissions.return_value = True

        # Execute
        set_user_roles(user_id, roles_data, mock_db, token_id)

        # Assert
        mock_audit_class.assert_called_once()
        call_kwargs = mock_audit_class.call_args.kwargs
        assert f"Updated roles: {[str(role) for role in roles_data.roles]}" == call_kwargs["action"]
        assert user_id == call_kwargs["user_id"]
        assert UUID(token_id) == call_kwargs["modified_by_user_id"]
        mock_db.add.assert_called_with(mock_audit_class.return_value)
