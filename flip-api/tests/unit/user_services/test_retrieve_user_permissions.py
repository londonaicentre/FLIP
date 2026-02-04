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
from unittest.mock import patch

import pytest
from fastapi import HTTPException, status

from flip_api.db.models.user_models import Permission, PermissionRef, RolePermission, UserRole
from flip_api.domain.schemas.users import UserPermissionsResponse
from flip_api.user_services.retrieve_user_permissions import (
    get_user_permissions,
    has_role,
    retrieve_user_permissions,
    to_pascal_case,
)


@pytest.fixture
def test_token_id() -> uuid.UUID:
    """Fixture for a sample token UUID (requesting user)."""
    return uuid.uuid4()


@pytest.fixture
def sample_permissions() -> list[str]:
    """Fixture for sample permission names."""
    return [PermissionRef.CAN_APPROVE_PROJECTS, PermissionRef.CAN_MANAGE_USERS]


@pytest.fixture
def sample_permission_objects(sample_permissions) -> list[Permission]:
    """Fixture for sample Permission model objects."""
    return [Permission(id=uuid.uuid4(), permission_name=name) for name in sample_permissions]


@pytest.fixture
def sample_role_permission(sample_permission_objects) -> list[RolePermission]:
    """Fixture for sample RolePermission model objects."""
    return [
        RolePermission(role_id=uuid.uuid4(), permission_id=permission.id) for permission in sample_permission_objects
    ]


@pytest.fixture
def mock_logger():
    """Fixture for mocking the logger."""
    with patch("flip_api.user_services.retrieve_user_permissions.logger") as mock_log:
        yield mock_log


# --- Tests for has_role ---


def test_has_role_user_has_role(mock_db_session_with_exec, user_id):
    """Test has_role returns True when user has a role."""
    mock_session, mock_exec = mock_db_session_with_exec
    mock_exec.first.return_value = UserRole(user_id=user_id, role_id=uuid.uuid4())

    result = has_role(user_id, mock_session)

    assert result is True
    mock_session.exec.assert_called_once()
    # Check the query structure if needed


def test_has_role_user_has_no_role(mock_db_session_with_exec, user_id):
    """Test has_role returns False when user has no roles."""
    mock_session, mock_exec = mock_db_session_with_exec
    mock_exec.first.return_value = None

    result = has_role(user_id, mock_session)

    assert result is False
    mock_session.exec.assert_called_once()


# --- Tests for get_user_permissions ---


def test_get_user_permissions_success(
    mock_db_session_with_exec, user_id, sample_role_permission, sample_permission_objects, sample_permissions
):
    """Test get_user_permissions returns correct permission names."""
    mock_session, mock_exec = mock_db_session_with_exec
    roles = [UserRole(user_id=user_id, role_id=uuid.uuid4())]
    mock_exec.all.side_effect = [roles, sample_role_permission, sample_permission_objects]

    result = get_user_permissions(user_id, mock_session)
    permission_names = [perm.permission_name for perm in result]

    assert permission_names == sample_permissions


def test_get_user_permissions_no_permissions(mock_db_session_with_exec, user_id):
    """Test get_user_permissions returns empty list when no permissions found."""
    mock_session, mock_exec = mock_db_session_with_exec
    mock_exec.all.return_value = []

    result = get_user_permissions(user_id, mock_session)

    assert result == []
    mock_session.exec.assert_called_once()


@patch("flip_api.user_services.retrieve_user_permissions.get_user_permissions")
@patch("flip_api.user_services.retrieve_user_permissions.has_role")
def test_retrieve_user_permissions_success(
    mock_has_role,
    mock_get_permissions,
    mock_db_session_with_exec,
    mock_logger,
    user_id,
    sample_permissions,
):
    """Test successful retrieval of user permissions."""
    mock_session, _ = mock_db_session_with_exec
    requesting_user_id = user_id  # User requests their own permissions

    # Setup mocks
    mock_has_role.return_value = True  # User has roles
    # Mock both calls to get_user_permissions
    mock_get_permissions.return_value = [Permission(permission_name=perm) for perm in sample_permissions]

    response = retrieve_user_permissions(user_id=user_id, db=mock_session, token_id=requesting_user_id)

    assert isinstance(response, UserPermissionsResponse)
    assert response.permissions == [to_pascal_case(perm.value) for perm in sample_permissions]
    mock_has_role.assert_called_once_with(user_id, mock_session)
    assert mock_get_permissions.call_count == 1
    mock_get_permissions.assert_any_call(user_id, mock_session)
    mock_logger.info.assert_called_once_with(f"Successfully retrieved permissions for user {user_id}")
    mock_logger.error.assert_not_called()
    mock_logger.warning.assert_not_called()
    mock_logger.exception.assert_not_called()


@patch("flip_api.user_services.retrieve_user_permissions.get_user_permissions")
def test_retrieve_user_permissions_forbidden(
    mock_get_permissions,
    mock_db_session_with_exec,
    mock_logger,
    user_id,
    test_token_id,  # Different from user_id
    sample_permissions,
):
    """Test 403 Forbidden when token ID does not match user ID and check fails."""
    mock_session, _ = mock_db_session_with_exec
    assert user_id != test_token_id

    # Setup mocks
    mock_get_permissions.return_value = sample_permissions  # First call before check

    with pytest.raises(HTTPException) as exc_info:
        retrieve_user_permissions(user_id=user_id, db=mock_session, token_id=test_token_id)

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert "User ID does not match token ID" in exc_info.value.detail
    mock_logger.error.assert_called_once_with(
        f"User {test_token_id} attempted to access permissions for user {user_id}."
    )


@patch("flip_api.user_services.retrieve_user_permissions.get_user_permissions")
@patch("flip_api.user_services.retrieve_user_permissions.has_role")
def test_retrieve_user_permissions_not_found(
    mock_has_role,
    mock_get_permissions,
    mock_db_session_with_exec,
    mock_logger,
    user_id,
    sample_permissions,
):
    """Test 404 Not Found when user has no roles."""
    mock_session, _ = mock_db_session_with_exec
    requesting_user_id = user_id

    # Setup mocks

    mock_get_permissions.return_value = sample_permissions  # First call before check
    mock_has_role.return_value = False  # User has no roles

    with pytest.raises(HTTPException) as exc_info:
        retrieve_user_permissions(user_id=user_id, db=mock_session, token_id=requesting_user_id)

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
    assert "does not exist or does not have a role assigned" in exc_info.value.detail
    mock_has_role.assert_called_once_with(user_id, mock_session)
    mock_logger.warning.assert_called_once_with(f"User {user_id} not found or has no roles assigned.")


@patch("flip_api.user_services.retrieve_user_permissions.get_user_permissions")
@patch("flip_api.user_services.retrieve_user_permissions.has_role")
def test_retrieve_user_permissions_internal_error_has_role(
    mock_has_role,
    mock_get_permissions,
    mock_db_session_with_exec,
    mock_logger,
    user_id,
    sample_permissions,
):
    """Test 500 Internal Server Error when has_role raises an exception."""
    mock_session, _ = mock_db_session_with_exec
    requesting_user_id = user_id
    db_error = Exception("Database connection error")

    # Setup mocks

    mock_get_permissions.return_value = sample_permissions
    mock_has_role.side_effect = db_error  # has_role fails

    with pytest.raises(HTTPException) as exc_info:
        retrieve_user_permissions(user_id=user_id, db=mock_session, token_id=requesting_user_id)

    assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert "internal server error" in exc_info.value.detail.lower()
    mock_has_role.assert_called_once_with(user_id, mock_session)
    mock_logger.exception.assert_called_once_with(
        f"An unexpected error occurred while retrieving permissions for user {user_id}: {db_error}"
    )


@patch("flip_api.user_services.retrieve_user_permissions.get_user_permissions")
@patch("flip_api.user_services.retrieve_user_permissions.has_role")
def test_retrieve_user_permissions_internal_error_get_permissions(
    mock_has_role,
    mock_get_permissions,
    mock_db_session_with_exec,
    mock_logger,
    user_id,
    sample_permissions,
):
    """Test 500 Internal Server Error when second get_user_permissions call fails."""
    mock_session, _ = mock_db_session_with_exec
    requesting_user_id = user_id
    db_error = Exception("Database query error")

    # Setup mocks

    mock_has_role.return_value = True
    # First call works (for permission check), second call fails
    mock_get_permissions.side_effect = [sample_permissions, db_error]

    with pytest.raises(HTTPException) as exc_info:
        retrieve_user_permissions(user_id=user_id, db=mock_session, token_id=requesting_user_id)

    assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert "internal server error" in exc_info.value.detail.lower()
    assert mock_get_permissions.call_count == 1
