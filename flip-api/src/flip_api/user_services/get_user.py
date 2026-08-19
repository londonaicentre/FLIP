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

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import EmailStr
from sqlmodel import Session

from flip_api.auth.auth_utils import has_any_permission, has_permissions
from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.db.models.user_models import PermissionRef
from flip_api.domain.schemas.users import CognitoUser, ProjectMemberLookup
from flip_api.utils.cognito_helpers import apply_user_profile, get_user_by_email_or_id, get_user_pool_id
from flip_api.utils.logger import logger

router = APIRouter(prefix="/users", tags=["user_services"])


# ROUTE ORDER BELOW IS LOAD-BEARING: FastAPI matches in declaration order and `/{user_id}` matches
# any single segment, so `/me` and `/lookup` must stay above it. Pinned by the route-ordering test
# in tests/unit/user_services/test_get_user.py, which also covers the pre-existing `/users/access`.


@router.get(
    "/me",
    response_model=CognitoUser,
    response_model_by_alias=True,
    summary="Get the caller's own profile",
    description="Get the authenticated caller's own user details. Requires no permission — the token is the caller.",
)
def get_current_user(
    request: Request,
    db: Session = Depends(get_session),
    token_id: UUID = Depends(verify_token),
) -> CognitoUser:
    """
    Get the authenticated caller's own user details.

    Deliberately carries no permission check: the bearer token *is* the authorization, so every
    authenticated user can read their own profile and no one else's. This is the only self-service
    read path — ``get_user`` below is administrative and has no self escape hatch (FLIP#907).

    Resolution is by the token's ``sub`` UUID rather than by email, so the caller cannot influence
    which record is returned.

    Args:
        request (Request): FastAPI request object for headers.
        db (Session): Database session.
        token_id (UUID): User ID from authentication token.

    Returns:
        CognitoUser: The caller's own user details.

    Raises:
        HTTPException: 404 if the token's subject has no matching Cognito record.
    """
    user_pool_id = get_user_pool_id(request)
    user = get_user_by_email_or_id(user_pool_id, user_id=token_id)

    return apply_user_profile(user, db)


@router.get(
    "/lookup",
    response_model=ProjectMemberLookup,
    response_model_by_alias=True,
    summary="Look up a prospective project member by email",
    description=(
        "Resolve an email address to the minimal identity needed to add a project member. "
        "Requires CAN_CREATE_PROJECTS or CAN_MANAGE_USERS permission."
    ),
)
def lookup_project_member(
    request: Request,
    email: EmailStr = Query(..., description="Email address of the prospective project member"),
    db: Session = Depends(get_session),
    token_id: UUID = Depends(verify_token),
) -> ProjectMemberLookup:
    """
    Resolve an email address to the minimal identity needed to add a project member.

    Requires CAN_CREATE_PROJECTS or CAN_MANAGE_USERS permission.

    Narrow by construction — see :class:`ProjectMemberLookup`. This route exists so Researchers,
    who need an email-to-id resolution to add a member, stay off the full-profile route.

    It does confirm to a Researcher whether a given address is registered, which is unavoidable:
    the UI has to be able to report "cannot be found" rather than silently drop a colleague from a
    project. That disclosure is one bit about an address the caller already holds, and it is
    bounded to Researchers and Admins — there is no self-signup on this platform.

    What is bounded is *what* one call discloses, not *how often* it may be asked: nothing
    rate-limits this route per caller, so a predictable address space stays walkable one guess at a
    time. Tracked in FLIP#961 rather than fixed here — the only stable per-caller key is the
    verified ``sub``, which means giving ``verify_token`` a ``Request`` to stash it on, i.e.
    changing the dependency every authenticated route uses.

    The address rides in the query string, which means it is retained in the CloudFront standard
    access logs (``cs-uri-query``) and the WAF logs (``httpRequest.args``, unredacted) — so the set
    of addresses a caller probed persists for those buckets' retention window. Kept as a ``GET``
    anyway: it is a read, ``/api/*`` is served by the caching-disabled policy so there is no CDN
    exposure, and the route this replaced carried the address in the path, which is logged just the
    same. A ``POST`` with the address in the body would keep it out of those logs, at the cost of a
    non-idempotent verb for a lookup — recorded here so the trade is not re-derived from scratch.

    Args:
        request (Request): FastAPI request object for headers.
        email (EmailStr): Email address of the prospective project member.
        db (Session): Database session.
        token_id (UUID): User ID from authentication token.

    Returns:
        ProjectMemberLookup: The user's ID, email and disabled status.

    Raises:
        HTTPException: 403 if the caller lacks permission, 404 if no user matches the address.
    """
    # Authorize BEFORE touching Cognito: an unauthorised caller must get the same 403 whether or
    # not the address exists, otherwise this becomes an account-existence oracle for Viewers.
    if not has_any_permission(token_id, [PermissionRef.CAN_CREATE_PROJECTS, PermissionRef.CAN_MANAGE_USERS], db):
        logger.error(f"User with ID: {token_id} attempted to look up a project member without permission")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not permitted to look up project members.",
        )

    user_pool_id = get_user_pool_id(request)

    try:
        user = get_user_by_email_or_id(user_pool_id, email=email)
    except HTTPException as e:
        if e.status_code == status.HTTP_404_NOT_FOUND:
            # The helper's message renders as "... or ID: None is not registered." for an
            # email lookup; give the UI something it can show verbatim instead.
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
        raise

    # Deliberately NOT apply_user_profile: that DB join is the only source of `name` and
    # `organisation`. Do not "tidy" this to match get_user below.
    return ProjectMemberLookup(
        id=user.id,
        email=user.email,
        is_disabled=user.is_disabled,
    )  # type: ignore[call-arg]


# [#114] ✅
@router.get(
    "/{user_id}",
    response_model=CognitoUser,
    response_model_by_alias=True,
    summary="Get any user's details",
    description="Get a user's full details by ID. Requires CAN_MANAGE_USERS permission.",
)
def get_user(
    user_id: UUID,
    request: Request,
    db: Session = Depends(get_session),
    token_id: UUID = Depends(verify_token),
) -> CognitoUser:
    """
    Get user details by ID.

    Requires CAN_MANAGE_USERS permission. There is intentionally no self escape hatch — callers
    reading their own profile use ``GET /users/me``, which keeps this route's rule to a single
    sentence and matches every sibling route under ``/users`` (FLIP#907).

    ``user_id`` is typed as a UUID rather than sniffed as "email or UUID" as it once was, matching
    every sibling route under ``/users``. A malformed id is therefore a 422 from parameter
    validation, which names the problem, rather than a bare 404 from the router.

    Looking a user up by email address was dropped with the same change: the member lookup moved to
    ``GET /users/lookup`` (FLIP#907), leaving this route no email callers.

    Args:
        user_id (UUID): User ID.
        request (Request): FastAPI request object for headers.
        db (Session): Database session.
        token_id (UUID): User ID from authentication token.

    Returns:
        CognitoUser: User details if found

    Raises:
        HTTPException: If the caller lacks permission, if the user is not found, or if there is an error getting the
                       user details.
    """
    try:
        # Checked before any Cognito call, so an unauthorised caller gets an identical 403 whether
        # or not the account exists.
        if not has_permissions(token_id, [PermissionRef.CAN_MANAGE_USERS], db):
            logger.error(f"User with ID: {token_id} was unable to manage users")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=f"User with ID: {token_id} was unable to manage users"
            )

        user_pool_id = get_user_pool_id(request)
        user = get_user_by_email_or_id(user_pool_id, user_id=user_id)

        return apply_user_profile(user, db)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting user: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Internal server error: {str(e)}"
        )
