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

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlmodel import Session

from flip_api.auth.auth_utils import has_permissions
from flip_api.auth.dependencies import verify_token
from flip_api.auth.identity import IdentityProvider, get_identity_provider
from flip_api.db.database import get_session
from flip_api.db.models.user_models import PermissionRef, UsersAudit
from flip_api.utils.logger import logger

router = APIRouter(prefix="/users", tags=["user_services"])


@router.post("/{user_id}/password/reset", response_model=dict[str, Any])
def reset_password_for_user(
    user_id: UUID,
    request: Request,
    db: Session = Depends(get_session),
    token_id: UUID = Depends(verify_token),
    idp: IdentityProvider = Depends(get_identity_provider),
) -> dict[str, Any]:
    """
    Email a user a way to set a new password.

    Server-side rather than a browser ForgotPassword so the provider can pick
    the right flow: a user who never completed their first sign-in gets their
    invite re-sent, where ForgotPassword would report success and send nothing.

    Args:
        user_id (UUID): ID of the user whose password should be reset.
        request (Request): FastAPI request object.
        db (Session): Database session.
        token_id (UUID): ID of the authenticated user performing the reset.
        idp (IdentityProvider): The configured identity provider.

    Returns:
        dict[str, Any]: Empty dictionary on success.

    Raises:
        HTTPException: If the caller lacks permission, the target user is not
        found, or the identity provider call fails.
    """
    try:
        if not has_permissions(token_id, [PermissionRef.CAN_MANAGE_USERS], db):
            logger.error(f"User with ID: {token_id} attempted to reset a password without permission")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"User with ID: {token_id} was unable to manage users",
            )

        idp.reset_password(idp.get_username(user_id))

        # The email has gone out, so an audit failure must not read as "reset failed": a retry
        # sends another email, and for an invited user a new temporary password voids the first.
        try:
            db.add(UsersAudit(action="Reset user password", user_id=user_id, modified_by_user_id=token_id))
            db.commit()
        except Exception as audit_err:
            db.rollback()
            logger.exception(
                f"Password reset email sent for user_id={user_id} (initiated by {token_id}) "
                f"but audit-row write failed; manual audit-log reconciliation required."
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Password reset partially succeeded; please verify and contact ops.",
            ) from audit_err

        return {}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error resetting user password for user_id={user_id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to reset user password"
        ) from e
