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
from flip_api.db.database import get_session
from flip_api.db.models.user_models import PermissionRef, UsersAudit
from flip_api.utils.cognito_helpers import get_user_pool_id, get_username, reset_user_mfa
from flip_api.utils.logger import logger

router = APIRouter(prefix="/users", tags=["user_services"])


@router.post("/{user_id}/mfa/reset", response_model=dict[str, Any])
def reset_mfa_for_user(
    user_id: UUID,
    request: Request,
    db: Session = Depends(get_session),
    token_id: UUID = Depends(verify_token),
) -> dict[str, Any]:
    """
    Reset a user's TOTP MFA preference and revoke their active sessions.

    Used by administrators to recover users who have lost their
    authenticator device. The Cognito pool is ``OPTIONAL`` (see the cognito
    module for rationale), so the next sign-in does NOT produce a
    ``CONTINUE_SIGN_IN_WITH_TOTP_SETUP`` challenge — Cognito signs the user
    in cleanly, and the app-layer MFA gate (``verify_token`` + the UI
    router guard on ``needsMfaEnrolment``) routes them through the
    post-auth enrolment page instead.

    Args:
        user_id (UUID): ID (Cognito ``sub``) of the user whose MFA should be
            reset. FastAPI validates the path segment, returning 422 on
            malformed input.
        request (Request): FastAPI request object.
        db (Session): Database session.
        token_id (UUID): ID of the authenticated user performing the reset.

    Returns:
        dict[str, Any]: Empty dictionary on success.

    Raises:
        HTTPException: If the caller lacks permission, the target user is not
        found, or the Cognito call fails.
    """
    try:
        if not has_permissions(token_id, [PermissionRef.CAN_MANAGE_USERS], db):
            logger.error(f"User with ID: {token_id} attempted to reset MFA without permission")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"User with ID: {token_id} was unable to manage users",
            )

        user_pool_id = get_user_pool_id(request)
        username = get_username(str(user_id), user_pool_id)

        reset_user_mfa(username, user_pool_id)

        # Cognito mutation succeeded; record the audit row. If the audit
        # commit fails, the security-relevant Cognito state has already
        # changed — surface a 500 so the operator knows reconciliation may
        # be needed, and log richly enough that they can do it. A retry is
        # safe (admin_set_user_mfa_preference is idempotent on the
        # SoftwareTokenMfaSettings payload, and admin_user_global_sign_out
        # is idempotent once sessions are already revoked).
        try:
            db.add(UsersAudit(action="Reset user MFA", user_id=user_id, modified_by_user_id=token_id))
            db.commit()
        except Exception as audit_err:
            db.rollback()
            logger.exception(
                f"Cognito MFA reset succeeded for user_id={user_id} (initiated by {token_id}) "
                f"but audit-row write failed; manual audit-log reconciliation required."
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="MFA reset partially succeeded; please verify and contact ops.",
            ) from audit_err

        return {}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error resetting user MFA for user_id={user_id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to reset user MFA"
        ) from e
