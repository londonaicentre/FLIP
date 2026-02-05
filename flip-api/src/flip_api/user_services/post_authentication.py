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

from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

from flip_api.db.models.user_models import User
from flip_api.utils.logger import logger


def sync_user_on_authentication(user_id: UUID, email: str, db: Session) -> User:
    """
    Synchronizes user information from Cognito post-authentication into the database.
    Inserts the user if they don't exist, or updates their email if it has changed.

    Args:
        user_id: The user's unique identifier (sub) from Cognito.
        email: The user's email address from Cognito.
        db: The database session.

    Raises:
        SQLAlchemyError: If there is a database error during the operation.
        Exception: For any other unexpected errors.

    Returns:
        flip.db.models.user_models.User: The up-to-date User instance.
    """
    try:
        # Check if user exists
        user = db.get(User, user_id)

        if not user:
            logger.info(f"Creating new user {user_id} with email {email}")
            user = User(id=user_id, email=email)
            db.add(user)
            db.commit()
            db.refresh(user)
            return user

        # User exists — update if email changed
        if user.email != email:
            logger.info(f"Updating email for user {user_id}: {user.email} → {email}")
            user.email = email
            db.commit()
            db.refresh(user)
        else:
            logger.debug(f"User {user_id} already synced and up-to-date.")

        return user

    except SQLAlchemyError as e:
        db.rollback()
        logger.exception(f"Database error syncing user {user_id}: {e}")
        raise

    except Exception as e:
        db.rollback()
        logger.exception(f"Unexpected error syncing user {user_id}: {e}")
        raise
