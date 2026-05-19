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
from sqlmodel import Session, col, delete

from flip_api.auth.auth_utils import has_permissions
from flip_api.auth.dependencies import verify_token
from flip_api.config import get_settings
from flip_api.db.database import get_session
from flip_api.db.models.user_models import PermissionRef, UserRole, UsersAudit
from flip_api.utils.cognito_helpers import delete_cognito_user, get_username
from flip_api.utils.logger import logger

router = APIRouter(prefix="/users", tags=["user_services"])

# TODO: Add a revoke token function to invalidate the user's token after deletion (figure if this should be here or in
# token/security/auth service)


# TODO [#114] This endpoint was not defined in the old repo, it was run as part of the 'registerUser' step function.
@router.delete("/{user_id}", response_model=dict[str, Any])
def delete_user(
    user_id: UUID,
    request: Request,
    db: Session = Depends(get_session),
    token_id: UUID = Depends(verify_token),
) -> dict[str, Any]:
    """
    Delete a user from Cognito and revoke their role grants.

    DB cleanup (delete role grants + write audit row) commits before the
    Cognito delete so a Cognito failure leaves a Cognito user with no
    app-side authority — preferable to dangling role grants on a deleted
    user. Idempotent: if the Cognito user is already gone, the DB-side
    cleanup still runs so any ghost role grants are reaped.

    Args:
        user_id (UUID): Cognito ``sub`` of the user to delete. FastAPI
            validates the path segment, returning 422 on malformed input.
        request (Request): The FastAPI request object.
        db (Session): The database session.
        token_id (UUID): ID of the authenticated user performing the delete.

    Returns:
        dict[str, Any]: Empty dictionary on success.

    Raises:
        HTTPException: If the caller lacks permission or an unexpected error
            (other than a Cognito 404) occurs.
    """
    try:
        # Check if user has permission to manage users
        if not has_permissions(token_id, [PermissionRef.CAN_MANAGE_USERS], db):
            logger.error(f"User with ID: {token_id} was unable to manage users")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=f"User with ID: {token_id} was unable to manage users"
            )

        user_pool_id = get_settings().AWS_COGNITO_USER_POOL_ID

        # Look up the Cognito username (email). `get_username` raises 404
        # if the sub is gone — treat that as "Cognito side already cleaned
        # up; still drop any ghost role grants on the DB side".
        try:
            username: str | None = get_username(str(user_id), user_pool_id)
        except HTTPException as exc:
            if exc.status_code != status.HTTP_404_NOT_FOUND:
                raise
            username = None

        # DB-side cleanup commits first so a subsequent Cognito failure
        # cannot leave dangling role grants on a deleted user.
        db.execute(delete(UserRole).where(col(UserRole.user_id) == user_id))
        db.add(UsersAudit(action="Deleted user", user_id=user_id, modified_by_user_id=token_id))
        db.commit()

        if username is not None:
            try:
                delete_cognito_user(username, user_pool_id)
            except HTTPException as cognito_err:
                # Half-deleted state: role grants are already gone and the
                # audit row is durable, but the Cognito user remains and can
                # still authenticate (with zero app authority). Surface the
                # state explicitly so the caller doesn't see a generic 500.
                logger.exception(
                    f"Cognito delete failed for user {user_id} ({username}) after DB cleanup; "
                    f"manual Cognito cleanup required."
                )
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=(
                        "Role grants revoked, but Cognito user deletion failed. "
                        "The user can still authenticate with zero app authority — "
                        "manual Cognito cleanup required."
                    ),
                ) from cognito_err

        return {}

    except HTTPException:
        raise
    except Exception as e:
        # Roll back any pending transaction so a failed db.commit() does
        # not leave the request-scoped session in an aborted state on the
        # way out. Matches the convention used elsewhere in user/cohort/
        # private services.
        db.rollback()
        logger.exception("Error deleting user")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error"
        ) from e
