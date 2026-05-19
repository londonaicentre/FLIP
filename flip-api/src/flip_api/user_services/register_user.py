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

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlmodel import Session

from flip_api.auth.auth_utils import has_permissions
from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.db.models.user_models import PermissionRef, UsersAudit
from flip_api.domain.interfaces.user import IRegisterUser, IUserResponse
from flip_api.utils.cognito_helpers import (
    create_cognito_user,
    delete_cognito_user,
    get_all_roles,
    get_user_pool_id,
    validate_roles,
)
from flip_api.utils.logger import logger

router = APIRouter(prefix="/users", tags=["user_services"])


def _rollback_cognito_on_audit_failure(email: str, user_pool_id: str, original_err: Exception) -> None:
    """Delete the just-created Cognito user after a failed audit-row write.

    The audit commit owns the failure path; this helper just attempts the
    Cognito-side rollback and translates a rollback failure into the more
    explicit "manual cleanup required" 500. Callers handle the post-rollback
    "Please try again" 500 themselves so the success-path 500 stays at the
    main call site.

    Mirrors the ``_rollback_after_role_failure`` pattern in
    ``register_user_step_function`` so the two rollback shapes look the same.

    Args:
        email: Email of the Cognito user to delete (the username).
        user_pool_id: Cognito user pool ID.
        original_err: The audit-write exception, chained as ``__cause__`` if
            this helper raises so the operator sees the original trigger.

    Raises:
        HTTPException: 500 with a "manual cleanup required" detail iff the
            ``delete_cognito_user`` call itself fails. A successful rollback
            returns cleanly; the caller then raises the "Please try again"
            500.
    """
    try:
        delete_cognito_user(email, user_pool_id)
    except Exception:
        logger.exception(
            f"Failed to roll back Cognito user {email} after audit-write failure; "
            f"manual cleanup required."
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to register user; rollback also failed. "
                "Manual cleanup of the Cognito user required."
            ),
        ) from original_err


# TODO [#114] This endpoint was not defined in the old repo, it was run from the step function 'registerUser'.
@router.post("/", response_model=IUserResponse)
def register_user(
    user_data: IRegisterUser,
    request: Request,
    db: Session = Depends(get_session),
    token_id: UUID = Depends(verify_token),
):
    """
    Register a new user in Cognito.

    Cognito is the source of truth for user identity; we do not mirror users
    in a local table. Role assignment is a separate step (handled by
    ``/api/step/users``). This endpoint writes a ``UsersAudit`` row keyed by
    the new Cognito sub on success.

    Args:
        user_data (IRegisterUser): The user data to register (email + roles).
        request (Request): The FastAPI request object.
        db (Session): The database session.
        token_id (UUID): ID of the authenticated user making the request.

    Returns:
        IUserResponse: Created user data including the new Cognito sub.

    Raises:
        HTTPException: If the user does not have permission to register a
        user, if the email is already registered, or if Cognito rejects the
        create.
    """
    try:
        # Check permissions
        if not has_permissions(token_id, [PermissionRef.CAN_MANAGE_USERS], db):
            logger.error(f"User with ID: {token_id} attempted to register a user without permission")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=f"User with ID: {token_id} was unable to register a user"
            )

        # Validate roles
        available_roles = get_all_roles(db)
        validate_roles(user_data.roles, available_roles)

        # Get user pool ID
        user_pool_id = get_user_pool_id(request)

        # Create user in Cognito
        user_id = create_cognito_user(user_data.email, user_pool_id)

        # Audit the registration. Cognito is the source of truth for the
        # user identity itself; UsersAudit captures the actor + timestamp
        # for forensic purposes.
        try:
            db.add(UsersAudit(action="Registered user", user_id=user_id, modified_by_user_id=token_id))
            db.commit()
        except Exception as audit_err:
            db.rollback()
            logger.exception(f"Error writing audit row for new user {user_data.email}")
            # Cognito accepted the user before the audit-row write failed.
            # Roll the Cognito side back so the next retry doesn't hit
            # UsernameExistsException; the helper raises its own 500 (with a
            # distinct detail) if the rollback itself fails, otherwise we
            # fall through to the retryable "Please try again" 500.
            _rollback_cognito_on_audit_failure(user_data.email, user_pool_id, audit_err)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to register user. Please try again.",
            ) from audit_err

        return IUserResponse(
            email=user_data.email,
            roles=user_data.roles,
            user_id=user_id,
        )  # type: ignore[call-arg]

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error registering user")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error"
        ) from e
