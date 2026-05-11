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

from fastapi import HTTPException, status
from sqlmodel import Session, select

from flip_api.config import get_settings
from flip_api.db.models.user_models import RoleRef, UserRole
from flip_api.utils.cognito_helpers import (
    get_user_by_email_or_id,
)
from flip_api.utils.constants import ADMIN_EMAIL_1, ADMIN_EMAIL_2, ADMIN_EMAIL_3, OBSERVER_EMAIL, RESEARCHER_EMAIL
from flip_api.utils.logger import logger


def ensure_user_and_role(email: str, role_ref: RoleRef, session: Session) -> None:
    """Look up the Cognito user for ``email`` and grant them ``role_ref``.

    Cognito is the source of truth for user identity, so this function does
    not create or maintain a local users row. It only ensures the
    ``user_role`` grant exists for the Cognito sub corresponding to the
    given email.

    Args:
        email (str): The user's email, used to look up the corresponding Cognito user.
        role_ref (RoleRef): The role to assign to the user if they don't already have it.
        session (Session): The SQLModel session used for DB reads and writes.
    """
    user_pool_id = get_settings().AWS_COGNITO_USER_POOL_ID

    # 1️⃣ Try to get the user from Cognito
    try:
        cognito_user = get_user_by_email_or_id(user_pool_id=user_pool_id, email=email)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            logger.warning(
                "Skipping seed user %s with role %s because the user does not exist in Cognito.",
                email,
                role_ref.name,
            )
            return
        raise
    user_id = cognito_user.id
    logger.debug(f"Found Cognito user {email} with sub {user_id}")

    # 2️⃣ Ensure the user has the expected role
    role_exists = session.exec(
        select(UserRole).where(UserRole.user_id == user_id).where(UserRole.role_id == role_ref.value)
    ).first()

    if not role_exists:
        logger.info(f"Assigning role {role_ref.name} to {email}")
        session.add(UserRole(user_id=user_id, role_id=role_ref.value))
        session.commit()


def _ensure_user_and_role_resilient(email: str, role_ref: RoleRef, session: Session) -> None:
    """Run ``ensure_user_and_role`` but tolerate transient Cognito-side HTTP failures.

    Seeding now reads from Cognito on every boot. A 5xx blip mid-deploy would
    otherwise couple flip-api liveness to Cognito read availability — log the skip
    loudly and continue with the remaining users instead.

    Definitive 4xx failures (e.g. 400 "no email/id provided", 403 if a future
    auth gate is added) still propagate: those are config / programming errors
    that should fail boot loudly rather than producing a platform with quietly
    missing grants.

    Args:
        email (str): The user's email used to look up the corresponding Cognito user.
        role_ref (RoleRef): The role to grant if missing.
        session (Session): The SQLModel session used for DB reads and writes.
    """
    try:
        ensure_user_and_role(email, role_ref, session)
    except HTTPException as exc:
        if exc.status_code < status.HTTP_500_INTERNAL_SERVER_ERROR:
            raise
        logger.warning(
            "Skipping seed for %s with role %s due to Cognito read failure (status=%s); "
            "platform will boot without this grant — investigate if it persists.",
            email,
            role_ref.name,
            exc.status_code,
        )


def seed_main_users(session: Session) -> None:
    """
    Seed role grants for the well-known admin/researcher/observer emails.

    Resolves each email to its Cognito sub and ensures the corresponding
    ``user_role`` row exists. No local users-table state is created.

    Args:
        session (Session): The SQLModel session used for DB reads and writes.
    """
    logger.debug("Seeding main users...")

    # Ensure the Admin role grant for each well-known admin email.
    _ensure_user_and_role_resilient(ADMIN_EMAIL_1, RoleRef.ADMIN, session)
    _ensure_user_and_role_resilient(ADMIN_EMAIL_2, RoleRef.ADMIN, session)
    _ensure_user_and_role_resilient(ADMIN_EMAIL_3, RoleRef.ADMIN, session)

    # Ensure the Researcher role grant.
    _ensure_user_and_role_resilient(RESEARCHER_EMAIL, RoleRef.RESEARCHER, session)

    # Ensure the Observer role grant.
    _ensure_user_and_role_resilient(OBSERVER_EMAIL, RoleRef.OBSERVER, session)

    logger.info("✅ Finished seeding main users.")
