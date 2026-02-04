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
from http import HTTPStatus
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from flip_api.db.models.user_models import PermissionRef
from flip.domain.interfaces.role import IRole, IRolesResponse
from flip.role_services.get_roles import get_roles


@pytest.fixture
def mock_token_id():
    """Fixture to provide a mock token ID for testing."""
    return uuid.uuid4()


def test_get_roles_success(mock_token_id):
    # Arrange
    mock_session = MagicMock()

    expected_roles_data = [
        (uuid.uuid4(), "Admin", "Administrator role"),
        (uuid.uuid4(), "User", "Standard user role"),
    ]
    mock_session.exec.return_value = expected_roles_data

    expected_roles = [
        IRole(id=str(role_data[0]), name=str(role_data[1]), description=role_data[2])
        for role_data in expected_roles_data
    ]
    expected_response = IRolesResponse(roles=expected_roles)

    with (
        patch("flip.role_services.get_roles.has_permissions", return_value=True) as mock_has_perms,
        patch("flip.role_services.get_roles.logger") as mock_logger,
    ):
        # Act
        response = get_roles(session=mock_session, token_id=mock_token_id)

        # Assert
        mock_has_perms.assert_called_once_with(mock_token_id, [PermissionRef.CAN_MANAGE_USERS], mock_session)
        mock_session.exec.assert_called_once()
        # Check the query construction (optional, but good for verifying the select statement)
        # args, _ = mock_session.exec.call_args
        # query = args[0]
        # assert "SELECT role.id, role.name, role.description" in
        # str(query.compile(compile_kwargs={"literal_binds": True}))
        # assert "ORDER BY role.name" in str(query.compile(compile_kwargs={"literal_binds": True}))

        assert response == expected_response
        mock_logger.info.assert_called_once_with(f"Output: {expected_response}")


def test_get_roles_no_permission(mock_token_id):
    # Arrange

    mock_session = MagicMock()

    with (
        patch("flip.role_services.get_roles.has_permissions", return_value=False) as mock_has_perms,
        patch("flip.role_services.get_roles.logger") as mock_logger,
    ):
        # Act & Assert
        with pytest.raises(HTTPException) as exc_info:
            get_roles(session=mock_session, token_id=mock_token_id)

        assert exc_info.value.status_code == HTTPStatus.FORBIDDEN
        assert exc_info.value.detail == f"User with ID: {mock_token_id} does not have permission"
        mock_has_perms.assert_called_once_with(mock_token_id, [PermissionRef.CAN_MANAGE_USERS], mock_session)
        mock_session.exec.assert_not_called()
        mock_logger.error.assert_not_called()  # Should not log error for permission denied


def test_get_roles_database_error(mock_token_id):
    # Arrange

    mock_session = MagicMock()
    db_error = Exception("Database connection failed")
    mock_session.exec.side_effect = db_error

    with (
        patch("flip.role_services.get_roles.has_permissions", return_value=True) as mock_has_perms,
        patch("flip.role_services.get_roles.logger") as mock_logger,
    ):
        # Act & Assert
        with pytest.raises(HTTPException) as exc_info:
            get_roles(session=mock_session, token_id=mock_token_id)

        assert exc_info.value.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
        assert exc_info.value.detail == "Internal server error"
        mock_has_perms.assert_called_once_with(mock_token_id, [PermissionRef.CAN_MANAGE_USERS], mock_session)
        mock_session.exec.assert_called_once()
        mock_logger.error.assert_called_once_with(f"Unhandled error: {str(db_error)}", exc_info=True)


def test_get_roles_no_roles_found(mock_token_id):
    # Arrange

    mock_session = MagicMock()

    mock_session.exec.return_value = []  # No roles in the database

    expected_response = IRolesResponse(roles=[])

    with (
        patch("flip.role_services.get_roles.has_permissions", return_value=True) as mock_has_perms,
        patch("flip.role_services.get_roles.logger") as mock_logger,
    ):
        # Act
        response = get_roles(session=mock_session, token_id=mock_token_id)

        # Assert
        mock_has_perms.assert_called_once_with(mock_token_id, [PermissionRef.CAN_MANAGE_USERS], mock_session)
        mock_session.exec.assert_called_once()
        assert response == expected_response
        mock_logger.info.assert_called_once_with(f"Output: {expected_response}")
