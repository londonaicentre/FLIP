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

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, col, delete, select

from flip_api.auth.auth_utils import has_permissions
from flip_api.auth.dependencies import verify_token
from flip_api.config import get_settings
from flip_api.db.database import get_session
from flip_api.db.models.user_models import PermissionRef, Role, UserRole, UsersAudit
from flip_api.domain.interfaces.user import IRoles
from flip_api.utils.cognito_helpers import get_username, validate_roles
from flip_api.utils.logger import logger

router = APIRouter(prefix="/users", tags=["user_services"])


@router.post("/{user_id}/roles", response_model=IRoles)
def set_user_roles(
    user_id: UUID,
    roles_data: IRoles,
    db: Session = Depends(get_session),
    token_id: UUID = Depends(verify_token),
) -> IRoles:
    """
    Set roles for a user.

    User existence is validated against Cognito (the source of truth) rather
    than a local DB row.

    Args:
        user_id (UUID): The ID of the user to update roles for.
        roles_data (IRoles): The roles data containing a list of role IDs to assign to the user.
        db (Session): The database session.
        token_id (UUID): The ID of the user making the request, used for permission checks.

    Returns:
        IRoles: The updated roles data for the user.

    Raises:
        HTTPException: 403 if the caller lacks permission, 404 if the target user is not in
            Cognito, 400 if any role is invalid, or 503 if the Cognito existence check itself
            failed (transient — caller may retry).
    """
    try:
        # Check permissions
        if not has_permissions(token_id, [PermissionRef.CAN_MANAGE_USERS], db):
            logger.error(f"User with ID: {token_id} attempted to update roles without permission")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"User with ID: {token_id} was unable to update a user's roles",
            )

        # Validate user existence against Cognito (the source of truth — no
        # local users table). 404 = genuinely-not-found; 5xx = Cognito read
        # failure. Surface them as distinct status codes so callers (in
        # particular ``register_user_step_function``) can decide whether to
        # treat this as a definitive "role assignment failed" (and roll back
        # the registration) or a transient "could not verify; retry later".
        # Non-404 client errors (e.g. a future 400 or 429 from Cognito) must
        # propagate untouched so caller-side bugs and rate-limit signals
        # aren't masked behind a generic 503.
        try:
            get_username(str(user_id), get_settings().AWS_COGNITO_USER_POOL_ID)
        except HTTPException as exc:
            if exc.status_code == status.HTTP_404_NOT_FOUND:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"User with ID {user_id} not found",
                ) from exc
            if exc.status_code >= status.HTTP_500_INTERNAL_SERVER_ERROR:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Could not verify user existence in Cognito; please try again.",
                ) from exc
            raise

        user_roles_ids = roles_data.roles

        # Validate the requested role IDs against the Role table.
        role_ids_from_db = db.exec(select(Role.id)).all()
        role_ids: list[UUID] = [r for r in role_ids_from_db if r is not None]
        validate_roles(user_roles_ids, role_ids)

        logger.info(f"Setting roles for user {user_id}: {user_roles_ids}")

        # Single transaction: drop old grants, insert the new ones, write
        # audit. A failure between the delete and the insert previously
        # left the user with no roles silently; consolidating into one
        # commit means either everything lands or nothing does.
        db.execute(delete(UserRole).where(col(UserRole.user_id) == user_id))
        db.add_all([UserRole(user_id=user_id, role_id=role_id) for role_id in user_roles_ids])
        db.add(
            UsersAudit(
                action="Updated user roles",
                user_id=user_id,
                modified_by_user_id=token_id,
            )
        )
        db.commit()

        return roles_data

    except HTTPException:
        raise
    except Exception as e:
        # Roll back any pending transaction so a failed db.commit() (or any
        # error after the DELETE/INSERT have been buffered) cannot leave
        # the request-scoped session in an aborted state on the way out.
        # Matches the convention used elsewhere in user/cohort/private
        # services.
        db.rollback()
        logger.exception("Error setting user roles")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error"
        ) from e
