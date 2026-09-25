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

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from flip_api.auth.identity import IdentityProvider, get_identity_provider
from flip_api.auth.token_verifier import verify_access_token
from flip_api.config import get_settings
from flip_api.utils.logger import logger

security = HTTPBearer()


def verify_token(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    idp: IdentityProvider = Depends(get_identity_provider),
) -> UUID:
    """
    Verify a bearer token and enforce that the caller has TOTP MFA enabled.

    Token verification is provider-agnostic (:mod:`flip_api.auth.token_verifier`).
    The MFA requirement is checked at the application boundary (rather than
    at the identity provider) so admin resets take effect immediately — see
    the comment on ``aws_cognito_user_pool.flip_user_pool`` in the cognito
    Terraform module for the full rationale. MFA-bootstrap endpoints use
    :func:`verify_token_no_mfa` instead.

    Args:
        credentials (HTTPAuthorizationCredentials): Bearer credentials from
            the incoming request.
        idp (IdentityProvider): The configured identity provider, asked for
            the caller's MFA state.

    Returns:
        UUID: The user ID (``sub`` claim) from the verified token.

    Raises:
        HTTPException: 401 if the token is invalid, expired, or missing
            claims; 403 if the caller has not enrolled TOTP.
    """
    identity = verify_access_token(credentials.credentials)

    # ENFORCE_MFA=False is dev-only opt-out set in compose.development.yml;
    # stag/prod inherit the Settings default (True) and keep the gate.
    if not get_settings().ENFORCE_MFA:
        logger.debug(f"ENFORCE_MFA disabled — skipping MFA gate for user: {identity.sub}")
        return identity.sub

    if not idp.is_mfa_enabled(identity.username):
        logger.warning(f"User {identity.sub} hit MFA-gated route without active TOTP")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="MFA enrolment required",
        )

    logger.info(f"Token verified successfully for user: {identity.sub}")
    return identity.sub


def verify_token_no_mfa(credentials: HTTPAuthorizationCredentials = Depends(security)) -> UUID:
    """
    Verify a bearer token without requiring TOTP MFA.

    Reserved for the MFA bootstrap endpoints (status check, enrolment
    helpers) that a freshly-reset or newly-invited user needs to reach
    before they have an active authenticator. Every other route must use
    :func:`verify_token`.

    Args:
        credentials (HTTPAuthorizationCredentials): Bearer credentials from
            the incoming request.

    Returns:
        UUID: The user ID (``sub`` claim) from the verified token.

    Raises:
        HTTPException: 401 if the token is invalid, expired, or missing
            claims.
    """
    return verify_access_token(credentials.credentials).sub
