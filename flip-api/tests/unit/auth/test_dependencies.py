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

"""The MFA gate in ``verify_token`` / ``verify_token_no_mfa``.

Token verification itself is covered by :mod:`test_token_verifier` with real
signatures; here ``verify_access_token`` is stubbed and the identity provider
is a test double, so each test isolates the gate's own decisions.
"""

import uuid
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest
from fastapi import HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials

from flip_api.auth.dependencies import verify_token, verify_token_no_mfa
from flip_api.auth.identity import IdentityProvider
from flip_api.auth.token_verifier import VerifiedIdentity

PATCH_VERIFY = "flip_api.auth.dependencies.verify_access_token"
PATCH_SETTINGS = "flip_api.auth.dependencies.get_settings"


@pytest.fixture
def user_sub() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def credentials() -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials="dummy.jwt.token")


@pytest.fixture
def idp() -> MagicMock:
    return MagicMock(spec=IdentityProvider)


def _identity(user_sub: str) -> VerifiedIdentity:
    return VerifiedIdentity(sub=UUID(user_sub), username="user@example.com")


def test_verify_token_allows_mfa_enrolled_caller(credentials, user_sub, idp):
    """Happy path: JWT is valid and MFA is active."""
    idp.is_mfa_enabled.return_value = True
    with patch(PATCH_VERIFY, return_value=_identity(user_sub)):
        result = verify_token(credentials, idp=idp)

    assert str(result) == user_sub
    # The gate hands the provider the principal name from the token, never the sub.
    idp.is_mfa_enabled.assert_called_once_with("user@example.com")


def test_verify_token_rejects_caller_without_mfa(credentials, user_sub, idp):
    """The MFA gate: a valid JWT without active TOTP yields 403."""
    idp.is_mfa_enabled.return_value = False
    with patch(PATCH_VERIFY, return_value=_identity(user_sub)):
        with pytest.raises(HTTPException) as exc_info:
            verify_token(credentials, idp=idp)

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert "MFA enrolment required" in exc_info.value.detail


def test_verify_token_propagates_verification_failures(credentials, idp):
    """A rejected token never reaches the MFA lookup."""
    rejected = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not validate credentials")
    with patch(PATCH_VERIFY, side_effect=rejected):
        with pytest.raises(HTTPException) as exc_info:
            verify_token(credentials, idp=idp)

    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    idp.is_mfa_enabled.assert_not_called()


def test_verify_token_no_mfa_skips_gate(credentials, user_sub):
    """The bootstrap variant never consults a provider — it's for pre-enrolment callers."""
    with patch(PATCH_VERIFY, return_value=_identity(user_sub)):
        result = verify_token_no_mfa(credentials)

    assert str(result) == user_sub


def test_verify_token_skips_mfa_gate_when_enforce_mfa_is_false(credentials, user_sub, idp):
    """ENFORCE_MFA=False (dev-only compose override) must bypass the
    is_mfa_enabled round-trip so a never-enrolled dev user can hit
    MFA-gated endpoints without being forced through TOTP enrolment."""
    with patch(PATCH_VERIFY, return_value=_identity(user_sub)), patch(PATCH_SETTINGS) as mock_get_settings:
        mock_get_settings.return_value.ENFORCE_MFA = False

        result = verify_token(credentials, idp=idp)

    assert str(result) == user_sub
    # Crucial: skipping the gate also skips the provider round-trip.
    idp.is_mfa_enabled.assert_not_called()


def test_verify_token_enforces_gate_when_enforce_mfa_is_true(credentials, user_sub, idp):
    """ENFORCE_MFA=True (default, stag/prod) keeps the existing gate in
    place — regression coverage so the dev opt-out can't be accidentally
    widened into stag/prod."""
    idp.is_mfa_enabled.return_value = False
    with patch(PATCH_VERIFY, return_value=_identity(user_sub)), patch(PATCH_SETTINGS) as mock_get_settings:
        mock_get_settings.return_value.ENFORCE_MFA = True

        with pytest.raises(HTTPException) as exc_info:
            verify_token(credentials, idp=idp)

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    idp.is_mfa_enabled.assert_called_once()
