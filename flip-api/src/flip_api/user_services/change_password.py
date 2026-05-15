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

from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from flip_api.auth.dependencies import verify_token
from flip_api.config import get_settings
from flip_api.utils.cognito_helpers import _cognito_client, revoke_token
from flip_api.utils.logger import logger

security = HTTPBearer()

router = APIRouter(prefix="/users", tags=["user_services"])


class ChangePasswordRequest(BaseModel):
    """Request body for changing the authenticated user's password."""

    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8)


class ChangePasswordResponse(BaseModel):
    """Response body after a successful password change."""

    detail: str


@router.post(
    "/me/password",
    response_model=ChangePasswordResponse,
    status_code=status.HTTP_200_OK,
    summary="Change own password",
    description=(
        "Change the authenticated user's password and revoke all "
        "existing sessions. Requires the current password for "
        "verification. After success the user must sign in again."
    ),
)
def change_own_password(
    body: ChangePasswordRequest,
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    token_id: UUID = Depends(verify_token),
) -> dict[str, str]:
    """
    Change the authenticated user's password via Cognito and revoke
    all active refresh tokens.

    Cognito's ``change_password`` verifies the current password and
    updates the credential; it does **not** invalidate active tokens,
    so this endpoint explicitly revokes the user's refresh token to
    force re-authentication with the new password.

    Args:
        body: The current and new password.
        request: FastAPI request object, used to read the optional
            ``X-Refresh-Token`` header for best-effort revocation.
        credentials: The Bearer token (used as Cognito's ``AccessToken``
            for the ``change_password`` call).
        token_id: The authenticated user's UUID (from ``verify_token``).

    Returns:
        dict[str, str]: A success message indicating the password was
        changed and the user should sign in again.

    Raises:
        HTTPException: 400 if the current password is wrong (Cognito
            ``NotAuthorizedException``), 500 on other Cognito errors.
    """
    cognito_client = _cognito_client()

    try:
        cognito_client.change_password(
            PreviousPassword=body.current_password,
            ProposedPassword=body.new_password,
            AccessToken=credentials.credentials,
        )
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        if error_code == "NotAuthorizedException":
            logger.warning(f"Wrong current password for user {token_id}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Current password is incorrect.",
            ) from e
        logger.exception(f"Failed to change password for user {token_id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to change password.",
        ) from e

    # Best-effort token revocation. The user may or may not send their
    # refresh token in the X-Refresh-Token header; if absent we
    # still succeed — Cognito's admin_user_global_sign_out is not
    # available to non-admin callers, and the frontend will call
    # PUT /users/revoke/{refresh_token} separately.
    refresh_token = request.headers.get("X-Refresh-Token", "")
    if refresh_token:
        try:
            revoke_token(refresh_token, get_settings().AWS_COGNITO_APP_CLIENT_ID)
        except HTTPException:
            logger.warning(
                f"Password changed for user {token_id} but token "
                f"revocation failed — user should sign out manually."
            )

    logger.info(f"Password changed successfully for user {token_id}")
    return {"detail": "Password changed successfully. Please sign in again."}


@router.put(
    "/revoke/{refresh_token}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a refresh token",
    description=(
        "Revoke a specific Cognito refresh token, invalidating its "
        "associated session. Used after password change or explicit "
        "sign-out to force re-authentication."
    ),
)
def revoke_own_token(
    refresh_token: str,
    token_id: UUID = Depends(verify_token),
) -> None:
    """
    Revoke a specific refresh token in Cognito.

    Used by the frontend after a password change or sign-out to
    invalidate the session server-side.

    Args:
        refresh_token: The Cognito refresh token to revoke.
        token_id: The authenticated user's UUID (from ``verify_token``).

    Raises:
        HTTPException: 500 if the Cognito revoke_token call fails.
    """
    revoke_token(refresh_token, get_settings().AWS_COGNITO_APP_CLIENT_ID)
