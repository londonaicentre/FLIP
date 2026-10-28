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

from flip_api.db.models.user_models import PermissionRef, UsersAudit
from flip_api.user_services.reset_user_password import reset_password_for_user

USERNAME = "user@example.com"


@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.fixture
def user_id():
    return uuid.uuid4()


@pytest.fixture
def token_id():
    return str(uuid.uuid4())


def test_permission_denied(fake_idp, mock_request, mock_db, user_id, token_id):
    with patch("flip_api.user_services.reset_user_password.has_permissions", return_value=False) as mock_perms:
        with pytest.raises(HTTPException) as exc_info:
            reset_password_for_user(user_id, mock_request, mock_db, token_id, idp=fake_idp)

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    mock_perms.assert_called_once_with(token_id, [PermissionRef.CAN_MANAGE_USERS], mock_db)
    fake_idp.reset_password.assert_not_called()


def test_user_not_found(fake_idp, mock_request, mock_db, user_id, token_id):
    fake_idp.get_username.side_effect = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not registered")

    with patch("flip_api.user_services.reset_user_password.has_permissions", return_value=True):
        with pytest.raises(HTTPException) as exc_info:
            reset_password_for_user(user_id, mock_request, mock_db, token_id, idp=fake_idp)

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
    fake_idp.reset_password.assert_not_called()
    mock_db.add.assert_not_called()


def test_password_reset_successfully(fake_idp, mock_request, mock_db, user_id, token_id):
    fake_idp.get_username.return_value = USERNAME

    with patch("flip_api.user_services.reset_user_password.has_permissions", return_value=True):
        result = reset_password_for_user(user_id, mock_request, mock_db, token_id, idp=fake_idp)

    assert result == {}
    fake_idp.reset_password.assert_called_once_with(USERNAME)
    audit_row = mock_db.add.call_args[0][0]
    assert isinstance(audit_row, UsersAudit)
    assert audit_row.action == "Reset user password"
    assert audit_row.user_id == user_id
    assert audit_row.modified_by_user_id == token_id
    mock_db.commit.assert_called_once()


def test_provider_error_is_500_without_leaking(fake_idp, mock_request, mock_db, user_id, token_id):
    fake_idp.get_username.return_value = USERNAME
    fake_idp.reset_password.side_effect = Exception("boom")

    with patch("flip_api.user_services.reset_user_password.has_permissions", return_value=True):
        with pytest.raises(HTTPException) as exc_info:
            reset_password_for_user(user_id, mock_request, mock_db, token_id, idp=fake_idp)

    assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert exc_info.value.detail == "Failed to reset user password"
    mock_db.add.assert_not_called()
