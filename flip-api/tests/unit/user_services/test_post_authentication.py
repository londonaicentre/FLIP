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

from unittest.mock import MagicMock, patch

import pytest

from flip_api.db.models.user_models import User
from flip_api.user_services.post_authentication import sync_user_on_authentication


@patch("flip_api.user_services.post_authentication.logger")
def test_new_user_creation(mock_logger):
    """
    Test that a new user is created when they don't exist in the database.
    """
    # Setup
    mock_session = MagicMock()
    user_id = "11111111-1111-1111-1111-111111111111"
    user_email = "new@example.com"

    mock_session.get.return_value = None  # Simulate user not found

    # Act
    result = sync_user_on_authentication(user_id, user_email, mock_session)

    # Assert
    mock_session.get.assert_called_once_with(User, user_id)
    mock_session.add.assert_called_once()
    added_user = mock_session.add.call_args[0][0]

    assert isinstance(added_user, User)
    assert added_user.id == user_id
    assert added_user.email == user_email

    mock_session.commit.assert_called_once()
    mock_session.refresh.assert_called_once_with(added_user)
    mock_session.rollback.assert_not_called()

    mock_logger.info.assert_called_once_with(f"Creating new user {user_id} with email {user_email}")
    mock_logger.exception.assert_not_called()
    assert result == added_user


@patch("flip_api.user_services.post_authentication.logger")
def test_existing_user_email_update(mock_logger):
    """
    Test that an existing user's email is updated if it differs.
    """
    # Setup
    mock_session = MagicMock()
    user_id = "22222222-2222-2222-2222-222222222222"
    old_email = "old@example.com"
    new_email = "updated@example.com"

    existing_user = User(id=user_id, email=old_email)
    mock_session.get.return_value = existing_user

    # Act
    result = sync_user_on_authentication(user_id, new_email, mock_session)

    # Assert
    mock_session.get.assert_called_once_with(User, user_id)
    assert existing_user.email == new_email

    mock_session.add.assert_not_called()  # No new add for existing user
    mock_session.commit.assert_called_once()
    mock_session.refresh.assert_called_once_with(existing_user)
    mock_session.rollback.assert_not_called()

    mock_logger.info.assert_called_once_with(f"Updating email for user {user_id}: {old_email} → {new_email}")
    mock_logger.exception.assert_not_called()
    assert result == existing_user


@patch("flip_api.user_services.post_authentication.logger")
def test_existing_user_email_no_update(mock_logger):
    """
    Test that no update occurs if the existing user's email matches.
    """
    # Setup
    mock_session = MagicMock()
    user_id = "33333333-3333-3333-3333-333333333333"
    email = "same@example.com"

    existing_user = User(id=user_id, email=email)
    mock_session.get.return_value = existing_user

    # Act
    result = sync_user_on_authentication(user_id, email, mock_session)

    # Assert
    mock_session.get.assert_called_once_with(User, user_id)
    mock_session.add.assert_not_called()
    mock_session.commit.assert_not_called()
    mock_session.refresh.assert_not_called()
    mock_session.rollback.assert_not_called()

    mock_logger.debug.assert_called_once_with(f"User {user_id} already synced and up-to-date.")
    mock_logger.exception.assert_not_called()
    assert result == existing_user


@patch("flip_api.user_services.post_authentication.logger")
def test_database_error_triggers_rollback(mock_logger):
    """
    Test that rollback is called and exception re-raised on SQLAlchemyError.
    """
    from sqlalchemy.exc import SQLAlchemyError

    mock_session = MagicMock()
    user_id = "44444444-4444-4444-4444-444444444444"
    user_email = "error@example.com"

    mock_session.get.side_effect = SQLAlchemyError("DB failure")

    with pytest.raises(SQLAlchemyError):
        sync_user_on_authentication(user_id, user_email, mock_session)

    mock_session.rollback.assert_called_once()
    mock_logger.exception.assert_called_once()
