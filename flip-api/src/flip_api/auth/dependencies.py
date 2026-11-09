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
from sqlmodel import Session, select

from flip_api.auth.identity import IdentityProvider, get_identity_provider
from flip_api.auth.token_verifier import verify_access_token
from flip_api.config import get_settings
from flip_api.db.database import get_session
from flip_api.db.models.user_models import UserProfile, UserRole
from flip_api.utils.logger import logger

security = HTTPBearer()


def require_provisioned_user(session: Session, user_id: UUID) -> None:
    """Refuse a valid token whose subject was never provisioned on this hub.

    A FLIP user is created by an admin (``register_user`` writes the ``UserProfile`` row,
    roles follow), so a subject with neither a profile nor a role row is an identity-provider
    account the hub never invited. The identity provider is configured to admit admin-created
    users only; this is the hub's own check, so a provider misconfiguration cannot turn a
    signed token into access.

    Args:
        session (Session): Database session.
        user_id (UUID): The verified token's ``sub``.

    Raises:
        HTTPException: 403 when the subject has no profile and no role on this hub.
    """
    if session.get(UserProfile, user_id) is not None:
        return
    if session.exec(select(UserRole.id).where(UserRole.user_id == user_id).limit(1)).first() is not None:
        return
    logger.warning(f"Token for unprovisioned subject {user_id} refused")
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is not registered on this platform")


def verify_token(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    idp: IdentityProvider = Depends(get_identity_provider),
    session: Session = Depends(get_session),
) -> UUID:
    """
    Verify a bearer token, require a provisioned user and enforce TOTP MFA.

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
        session (Session): Database session, for the provisioned-user check.

    Returns:
        UUID: The user ID (``sub`` claim) from the verified token.

    Raises:
        HTTPException: 401 if the token is invalid, expired, or missing
            claims; 403 if the subject is not a provisioned user or has not
            enrolled TOTP.
    """
    identity = verify_access_token(credentials.credentials)
    require_provisioned_user(session, identity.sub)

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


def verify_token_no_mfa(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    session: Session = Depends(get_session),
) -> UUID:
    """
    Verify a bearer token and require a provisioned user, without requiring TOTP MFA.

    Reserved for the MFA bootstrap endpoints (status check, enrolment
    helpers) that a freshly-reset or newly-invited user needs to reach
    before they have an active authenticator. Every other route must use
    :func:`verify_token`.

    Args:
        credentials (HTTPAuthorizationCredentials): Bearer credentials from
            the incoming request.
        session (Session): Database session, for the provisioned-user check.

    Returns:
        UUID: The user ID (``sub`` claim) from the verified token.

    Raises:
        HTTPException: 401 if the token is invalid, expired, or missing
            claims; 403 if the subject is not a provisioned user.
    """
    identity = verify_access_token(credentials.credentials)
    require_provisioned_user(session, identity.sub)
    return identity.sub
