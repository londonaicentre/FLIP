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

from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.domain.interfaces.user import IRegisterUser, IRegisterUserDto, IRoles
from flip_api.user_services.delete_user import delete_user
from flip_api.user_services.register_user import register_user
from flip_api.user_services.set_user_roles import set_user_roles
from flip_api.utils.logger import logger

router = APIRouter(prefix="/step", tags=["step_functions_services"])

# A 404 from set_user_roles immediately after admin_create_user is Cognito's
# ListUsers (sub-filter) propagation lag — the user definitely exists, we
# just created it. Treating it as definitive would tear down a valid
# registration. A 503 is the same shape (transient Cognito read failure).
_TRANSIENT_ROLE_ASSIGNMENT_STATUSES = (
    status.HTTP_404_NOT_FOUND,
    status.HTTP_503_SERVICE_UNAVAILABLE,
)


def _rollback_after_role_failure(
    *,
    user_id: UUID,
    user_email: str,
    role_err: BaseException,
    request: Request,
    db: Session,
    token_id: UUID,
) -> "HTTPException":
    """Roll back a just-registered Cognito user after role assignment failed definitively.

    Returns the ``HTTPException`` to raise at the call site (chained ``from
    role_err``). If ``delete_user`` raises, builds a detail that includes
    *both* failures so the operator can see why the rollback broke — a
    plain ``except Exception`` would discard a rollback ``HTTPException``'s
    own detail.

    Args:
        user_id (UUID): Cognito sub of the just-registered user.
        user_email (str): Email of the user, for forensic logging.
        role_err (BaseException): The role-assignment error that triggered
            the rollback.
        request (Request): FastAPI request, forwarded to ``delete_user``.
        db (Session): Database session.
        token_id (UUID): Authenticated caller's id.

    Returns:
        HTTPException: 500 to raise from the caller.
    """
    logger.exception(f"Failed to set user roles for user {user_id}; rolling back")
    try:
        delete_user(user_id=user_id, request=request, db=db, token_id=token_id)
    except Exception as rollback_err:
        # For HTTPException, prefer `.detail` (the operator-facing message);
        # for any other exception, str() is the closest equivalent. Catching
        # both here keeps the rollback-detail extraction in one place.
        rollback_detail = rollback_err.detail if isinstance(rollback_err, HTTPException) else str(rollback_err)
        logger.exception(
            f"Failed to roll back user {user_id} ({user_email}) after role assignment "
            f"failure; manual cleanup of Cognito + user_role required."
        )
        return HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"Failed to set user roles for user {user_id}; rollback also failed "
                f"({rollback_detail}). Manual cleanup required."
            ),
        )

    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=f"Failed to set user roles for user {user_id}. User has been deleted.",
    )


@router.post("/users", response_model=IRegisterUserDto, status_code=status.HTTP_201_CREATED)
def register_user_step_function_endpoint(
    request: Request,
    user_data: IRegisterUser,
    db: Session = Depends(get_session),
    token_id: UUID = Depends(verify_token),
):
    """
    Register a new user and assign roles.

    Two-phase: register the Cognito user, then assign roles. If role
    assignment fails *definitively* (4xx other than 404, or unexpected
    Exception), the Cognito user is rolled back via ``delete_user``. If
    role assignment fails *transiently* (HTTP 503, or HTTP 404 — Cognito
    ListUsers propagation lag immediately after admin_create_user) the
    rollback is skipped: the user has been registered and the operator
    (or a retry) can complete role assignment later. This avoids
    destroying valid registrations on a transient Cognito blip.

    Args:
        request (Request): The FastAPI request object.
        user_data (IRegisterUser): The user data to register, including email and roles.
        db (Session): The database session.
        token_id (UUID): The ID of the current user making the request.

    Returns:
        IRegisterUserDto: A DTO with the new user's id, email, and roles.

    Raises:
        HTTPException: If an error occurs during registration or role assignment.
    """
    try:
        logger.info(f"Registering user: {user_data.email}")

        register_response = register_user(
            user_data=user_data,
            request=request,
            db=db,
            token_id=token_id,
        )

        user_id = register_response.user_id
        roles = IRoles(roles=register_response.roles)

        logger.info(f"Setting roles for user {user_id}: {roles}")

        try:
            set_roles_response = set_user_roles(user_id=user_id, roles_data=roles, db=db, token_id=token_id)
            logger.info(f"Roles set successfully for user {user_id}: {set_roles_response}")

        except Exception as role_err:
            # Transient Cognito read failures (HTTP 404/503 from set_user_roles)
            # must NOT trigger the rollback — the user definitely exists, we
            # just created it. Re-raise so the caller can retry.
            if (
                isinstance(role_err, HTTPException)
                and role_err.status_code in _TRANSIENT_ROLE_ASSIGNMENT_STATUSES
            ):
                logger.warning(
                    f"Role assignment for user {user_id} could not be verified "
                    f"(transient Cognito read, status={role_err.status_code}); "
                    f"leaving user in place for retry."
                )
                raise

            raise _rollback_after_role_failure(
                user_id=user_id,
                user_email=user_data.email,
                role_err=role_err,
                request=request,
                db=db,
                token_id=token_id,
            ) from role_err

        logger.info(f"User {user_id} registered successfully with roles {roles}")

        return IRegisterUserDto(
            user_id=user_id,
            email=register_response.email,
            roles=register_response.roles,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unhandled error in register_user_endpoint")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to register user",
        ) from e
