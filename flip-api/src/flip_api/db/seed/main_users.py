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

from sqlmodel import Session, select

from flip_api.config import get_settings
from flip.db.models.user_models import RoleRef, User, UserRole
from flip.utils.cognito_helpers import (
    get_user_by_email_or_id,
)
from flip.utils.constants import ADMIN_EMAIL, RESEARCHER_EMAIL
from flip.utils.logger import logger


def ensure_user_and_role(email: str, role_ref: RoleRef, session: Session) -> None:
    """Fetch or create a Cognito + DB user and assign a role."""
    user_pool_id = get_settings().AWS_COGNITO_USER_POOL_ID

    # 1️⃣ Try to get the user from Cognito
    cognito_user = get_user_by_email_or_id(user_pool_id=user_pool_id, email=email)
    user_id = cognito_user.id
    logger.debug(f"Found Cognito user {email} with sub {user_id}")

    # 2️⃣ Ensure the user exists in the local DB
    db_user = session.exec(select(User).where(User.id == user_id)).first()
    if not db_user:
        logger.info(f"Creating local user for {email} with id {user_id}")
        db_user = User(id=user_id, email=email)
        session.add(db_user)
        session.commit()
        session.refresh(db_user)

    # 3️⃣ Ensure the user has the expected role
    role_exists = session.exec(
        select(UserRole).where(UserRole.user_id == db_user.id).where(UserRole.role_id == role_ref.value)
    ).first()

    if not role_exists:
        logger.info(f"Assigning role {role_ref.name} to {email}")
        new_role = UserRole(user_id=db_user.id, role_id=role_ref.value)
        session.add(new_role)
        session.commit()


def seed_main_users(session: Session) -> None:
    """
    Seed the admin and researcher users into the database.
    - Get the user from Cognito by email.
    - If the user does not exist in DB, create them with the correct Cognito sub.
    - Assign appropriate roles.
    """
    logger.debug("Seeding main users...")

    # Create / sync the admin user
    ensure_user_and_role(ADMIN_EMAIL, RoleRef.ADMIN, session)

    # Create / sync the researcher user
    ensure_user_and_role(RESEARCHER_EMAIL, RoleRef.RESEARCHER, session)

    logger.info("✅ Finished seeding main users.")
