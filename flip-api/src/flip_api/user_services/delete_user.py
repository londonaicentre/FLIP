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

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlmodel import Session, col, delete, select

from flip_api.auth.auth_utils import has_permissions
from flip_api.auth.dependencies import verify_token
from flip_api.auth.identity import IdentityProvider, get_identity_provider
from flip_api.db.database import get_session
from flip_api.db.models.main_models import Projects, ProjectUserAccess, Trust
from flip_api.db.models.user_models import PermissionRef, RoleRef, UserProfile, UserRole, UsersAudit
from flip_api.domain.schemas.users import CognitoUser
from flip_api.utils.logger import logger

router = APIRouter(prefix="/users", tags=["user_services"])

# TODO: Add a revoke token function to invalidate the user's token after deletion (figure if this should be here or in
# token/security/auth service)


def deletion_blockers(user_id: UUID, db: Session) -> list[str]:
    """Name what the user must hand over before they can be deleted (FLIP#1996).

    Args:
        user_id (UUID): The user to be deleted.
        db (Session): The database session.

    Returns:
        list[str]: One phrase per blocker, empty when the user can be deleted.
    """
    projects = db.exec(
        select(Projects.name).where(Projects.owner_id == user_id, col(Projects.deleted).is_(False))
    ).all()
    trusts = db.exec(
        select(Trust.name)
        .join(UserRole, col(UserRole.trust_id) == col(Trust.id))
        .where(UserRole.user_id == user_id, UserRole.role_id == RoleRef.TRUST_ADMIN.value)
    ).all()
    blockers = []
    if projects:
        blockers.append(f"owns project(s) {', '.join(sorted(projects))}")
    if trusts:
        blockers.append(f"is Trust Admin of {', '.join(sorted(trusts))}")
    return blockers


@router.delete("/{user_id}", response_model=dict[str, Any])
def delete_user(
    user_id: UUID,
    request: Request,
    db: Session = Depends(get_session),
    token_id: UUID = Depends(verify_token),
    idp: IdentityProvider = Depends(get_identity_provider),
) -> dict[str, Any]:
    """
    Delete a disabled user from the identity provider and revoke their role grants and project access.

    Refused (409) for the caller's own account, and while the user is still enabled, owns a live project or is a
    Trust Admin. The caller holds CAN_MANAGE_USERS, so refusing self-deletion also means the hub always keeps a user
    who can manage users. The
    profile row stays, marked deleted with only the name kept, so history reads "Former user (name)";
    the audit row records that name too.

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
        idp (IdentityProvider): The configured identity provider.

    Returns:
        dict[str, Any]: Empty dictionary on success.

    Raises:
        HTTPException: 403 if the caller lacks permission, 409 if the user cannot be deleted yet,
            500 on an unexpected error (other than a Cognito 404).
    """
    try:
        # Check if user has permission to manage users
        if not has_permissions(token_id, [PermissionRef.CAN_MANAGE_USERS], db):
            logger.error(f"User with ID: {token_id} was unable to manage users")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=f"User with ID: {token_id} was unable to manage users"
            )

        if user_id == token_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="You cannot delete your own account; ask another admin to delete it.",
            )

        # `get_user` raises 404 if the sub is gone — treat that as "provider side
        # already cleaned up; still drop any ghost role grants on the DB side".
        try:
            user: CognitoUser | None = idp.get_user(user_id=user_id)
        except HTTPException as exc:
            if exc.status_code != status.HTTP_404_NOT_FOUND:
                raise
            user = None

        if user is not None and not user.is_disabled:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Disable the user before deleting them.")
        if blockers := deletion_blockers(user_id, db):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"The user {' and '.join(blockers)}; hand these over before deleting them.",
            )

        # Keep the profile for history, with the name only.
        profile = db.get(UserProfile, user_id)
        name = (profile.name if profile else "") or (user.name if user else "")
        if profile is not None:
            profile.organisation = ""
            profile.deleted_at = profile.deleted_at or datetime.utcnow()
            db.add(profile)

        # DB-side cleanup commits first so a subsequent Cognito failure
        # cannot leave dangling role grants on a deleted user.
        db.execute(delete(UserRole).where(col(UserRole.user_id) == user_id))
        db.execute(delete(ProjectUserAccess).where(col(ProjectUserAccess.user_id) == user_id))
        db.add(UsersAudit(action="Deleted user", user_id=user_id, modified_by_user_id=token_id, user_name=name or None))
        db.commit()

        if user is not None:
            try:
                idp.delete_user(user.email)
            except HTTPException as cognito_err:
                # Half-deleted state: role grants are already gone and the
                # audit row is durable, but the Cognito user remains and can
                # still authenticate (with zero app authority). Surface the
                # state explicitly so the caller doesn't see a generic 500.
                logger.exception(
                    f"Identity-provider delete failed for user {user_id} ({user.email}) after DB cleanup; "
                    f"manual identity-provider cleanup required."
                )
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=(
                        "Role grants revoked, but identity-provider user deletion failed. "
                        "The user can still authenticate with zero app authority — "
                        "manual identity-provider cleanup required."
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
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error") from e
