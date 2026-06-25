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

import uuid
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi import HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials

from flip_api.auth.dependencies import _decode_verified_claims, _extract_user_id, verify_token, verify_token_no_mfa


@pytest.fixture
def user_sub():
    return str(uuid.uuid4())


@pytest.fixture
def credentials():
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials="dummy.jwt.token")


def _payload(user_sub: str, username: str = "user@example.com") -> dict:
    return {"sub": user_sub, "username": username, "token_use": "access"}


def test_verify_token_allows_mfa_enrolled_caller(credentials, user_sub):
    """Happy path: JWT is valid and MFA is active."""
    with (
        patch("flip_api.auth.dependencies._decode_verified_claims") as mock_decode,
        patch("flip_api.auth.dependencies.is_mfa_enabled") as mock_is_enabled,
    ):
        mock_decode.return_value = _payload(user_sub)
        mock_is_enabled.return_value = True

        result = verify_token(credentials)

        assert str(result) == user_sub
        mock_is_enabled.assert_called_once()


def test_verify_token_rejects_caller_without_mfa(credentials, user_sub):
    """The MFA gate: a valid JWT without active TOTP yields 403."""
    with (
        patch("flip_api.auth.dependencies._decode_verified_claims") as mock_decode,
        patch("flip_api.auth.dependencies.is_mfa_enabled") as mock_is_enabled,
    ):
        mock_decode.return_value = _payload(user_sub)
        mock_is_enabled.return_value = False

        with pytest.raises(HTTPException) as exc_info:
            verify_token(credentials)

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
        assert "MFA enrolment required" in exc_info.value.detail


def test_verify_token_rejects_token_missing_username(credentials, user_sub):
    """The MFA gate needs the Cognito Username claim; without it we 401 early."""
    with (
        patch("flip_api.auth.dependencies._decode_verified_claims") as mock_decode,
        patch("flip_api.auth.dependencies.is_mfa_enabled") as mock_is_enabled,
    ):
        mock_decode.return_value = {"sub": user_sub, "token_use": "access"}

        with pytest.raises(HTTPException) as exc_info:
            verify_token(credentials)

        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
        mock_is_enabled.assert_not_called()


def test_verify_token_no_mfa_skips_gate(credentials, user_sub):
    """The bootstrap variant never touches the MFA helper — it's for pre-enrolment callers."""
    with (
        patch("flip_api.auth.dependencies._decode_verified_claims") as mock_decode,
        patch("flip_api.auth.dependencies.is_mfa_enabled") as mock_is_enabled,
    ):
        mock_decode.return_value = _payload(user_sub)

        result = verify_token_no_mfa(credentials)

        assert str(result) == user_sub
        mock_is_enabled.assert_not_called()


def test_verify_token_skips_mfa_gate_when_enforce_mfa_is_false(credentials, user_sub):
    """ENFORCE_MFA=False (dev-only compose override) must bypass the
    is_mfa_enabled round-trip so a never-enrolled dev user can hit
    MFA-gated endpoints without being forced through TOTP enrolment."""
    with (
        patch("flip_api.auth.dependencies._decode_verified_claims") as mock_decode,
        patch("flip_api.auth.dependencies.is_mfa_enabled") as mock_is_enabled,
        patch("flip_api.auth.dependencies.get_settings") as mock_get_settings,
    ):
        mock_decode.return_value = _payload(user_sub)
        mock_get_settings.return_value.ENFORCE_MFA = False

        result = verify_token(credentials)

        assert str(result) == user_sub
        # Crucial: we must not have called the Cognito MFA lookup at all —
        # skipping the gate also skips the AdminGetUser round-trip.
        mock_is_enabled.assert_not_called()


def test_verify_token_enforces_gate_when_enforce_mfa_is_true(credentials, user_sub):
    """ENFORCE_MFA=True (default, stag/prod) keeps the existing gate in
    place — regression coverage so the dev opt-out can't be accidentally
    widened into stag/prod."""
    with (
        patch("flip_api.auth.dependencies._decode_verified_claims") as mock_decode,
        patch("flip_api.auth.dependencies.is_mfa_enabled") as mock_is_enabled,
        patch("flip_api.auth.dependencies.get_settings") as mock_get_settings,
    ):
        mock_decode.return_value = _payload(user_sub)
        mock_get_settings.return_value.ENFORCE_MFA = True
        mock_is_enabled.return_value = False

        with pytest.raises(HTTPException) as exc_info:
            verify_token(credentials)

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
        mock_is_enabled.assert_called_once()


def _make_signing_key():
    """Minimal stub: PyJWKClient.get_signing_key_from_jwt returns an object with a .key attr."""
    return MagicMock(key="signing-key")


def test_decode_raises_401_on_expired_signature(credentials):
    """Cognito-issued JWTs expire after an hour; an ExpiredSignatureError from
    jwt.decode must surface as 401, not leak as an uncaught exception."""
    with (
        patch("flip_api.auth.dependencies.PyJWKClient") as mock_jwks_cls,
        patch("flip_api.auth.dependencies.jwt.decode") as mock_decode,
    ):
        mock_jwks_cls.return_value.get_signing_key_from_jwt.return_value = _make_signing_key()
        mock_decode.side_effect = jwt.ExpiredSignatureError("token expired")

        with pytest.raises(HTTPException) as exc_info:
            _decode_verified_claims("expired.jwt.token")

        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert "expired" in exc_info.value.detail.lower()


def test_decode_raises_401_on_invalid_token(credentials):
    """Any other PyJWT validation failure (bad signature, malformed claims)
    must also be a 401 — we never want to let a bad token through as 500."""
    with (
        patch("flip_api.auth.dependencies.PyJWKClient") as mock_jwks_cls,
        patch("flip_api.auth.dependencies.jwt.decode") as mock_decode,
    ):
        mock_jwks_cls.return_value.get_signing_key_from_jwt.return_value = _make_signing_key()
        mock_decode.side_effect = jwt.InvalidTokenError("bad signature")

        with pytest.raises(HTTPException) as exc_info:
            _decode_verified_claims("bad.jwt.token")

        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert "credentials" in exc_info.value.detail.lower()


def test_decode_raises_500_on_unexpected_error(credentials):
    """A non-JWT, non-HTTP exception (e.g. JWKS network error) converts to
    500 with a generic message — we don't want to echo boto/urllib internals
    to the caller."""
    with patch("flip_api.auth.dependencies.PyJWKClient") as mock_jwks_cls:
        # Surface a generic Exception from the JWKS fetch path.
        mock_jwks_cls.return_value.get_signing_key_from_jwt.side_effect = RuntimeError(
            "connection refused to JWKS endpoint"
        )

        with pytest.raises(HTTPException) as exc_info:
            _decode_verified_claims("unused.jwt.token")

        assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        # Detail is deliberately generic — don't leak the RuntimeError text.
        assert "connection refused" not in exc_info.value.detail


def test_extract_user_id_rejects_missing_sub(user_sub):
    """A verified JWT payload without a 'sub' claim is structurally valid
    but useless to us — every downstream query keys off the user ID."""
    with pytest.raises(HTTPException) as exc_info:
        _extract_user_id({"token_use": "access", "username": "u@e.com"})

    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert "user identifier" in exc_info.value.detail.lower()


def test_extract_user_id_rejects_non_uuid_sub():
    """Cognito always gives sub as a UUID. A non-UUID string (corrupt pool
    config, or a forged token that slipped past signature check) must 401
    rather than raise an unhandled ValueError deeper in the stack."""
    with pytest.raises(HTTPException) as exc_info:
        _extract_user_id({"sub": "not-a-uuid", "token_use": "access"})

    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert "invalid user identifier" in exc_info.value.detail.lower()


# ---------------------------------------------------------------------------
# Issuer / token_use / client_id validation
#
# Cognito access tokens (the only token type flip-ui sends post-#344) carry
# `client_id`, not `aud`. The verifier rejects any other token_use value —
# `id`, `refresh`, or anything Cognito has yet to invent.
# ---------------------------------------------------------------------------

TEST_REGION = "eu-west-2"
TEST_USER_POOL_ID = "eu-west-2_TESTPOOL"
TEST_APP_CLIENT_ID = "test-app-client-id"
EXPECTED_ISSUER = f"https://cognito-idp.{TEST_REGION}.amazonaws.com/{TEST_USER_POOL_ID}"
EXPECTED_JWKS_URL = f"{EXPECTED_ISSUER}/.well-known/jwks.json"

PATCH_GET_SETTINGS = "flip_api.auth.dependencies.get_settings"
PATCH_PYJWK_CLIENT = "flip_api.auth.dependencies.PyJWKClient"
PATCH_JWT_DECODE = "flip_api.auth.dependencies.jwt.decode"


@pytest.fixture
def mock_settings():
    """Mock get_settings() with realistic Cognito values and ENFORCE_MFA=False
    so the audience/issuer assertions exercise only the JWT-validation path."""
    with patch(PATCH_GET_SETTINGS) as m:
        settings = MagicMock()
        settings.AWS_REGION = TEST_REGION
        settings.AWS_COGNITO_USER_POOL_ID = TEST_USER_POOL_ID
        settings.AWS_COGNITO_APP_CLIENT_ID = TEST_APP_CLIENT_ID
        settings.ENFORCE_MFA = False
        m.return_value = settings
        yield m


@pytest.fixture
def mock_jwks():
    with patch(PATCH_PYJWK_CLIENT) as jwks_client_cls:
        signing_key = MagicMock()
        signing_key.key = "fake-public-key"
        jwks_client = MagicMock()
        jwks_client.get_signing_key_from_jwt.return_value = signing_key
        jwks_client_cls.return_value = jwks_client
        yield jwks_client_cls


def _bearer(token: str = "fake.jwt.token") -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def _access_token_payload(sub: str | None = None, client_id: str = TEST_APP_CLIENT_ID) -> dict:
    return {
        "sub": sub or str(uuid4()),
        "client_id": client_id,
        "iss": EXPECTED_ISSUER,
        "token_use": "access",
        "username": "user@example.com",
        "exp": 9999999999,
    }


class TestVerifyTokenSuccess:
    def test_valid_access_token_returns_user_uuid(self, mock_settings, mock_jwks):
        sub = str(uuid4())
        with patch(PATCH_JWT_DECODE, return_value=_access_token_payload(sub=sub)):
            user_id = verify_token(_bearer())
        assert user_id == UUID(sub)

    def test_jwt_decode_called_with_issuer_and_algorithm(self, mock_settings, mock_jwks):
        with patch(PATCH_JWT_DECODE, return_value=_access_token_payload()) as decode:
            verify_token(_bearer())
        kwargs = decode.call_args.kwargs
        assert kwargs["algorithms"] == ["RS256"]
        assert kwargs["issuer"] == EXPECTED_ISSUER
        # Cognito access tokens don't carry `aud`, so PyJWT's audience check is
        # disabled — `client_id` is verified manually inside `_decode_cognito_jwt`.
        assert kwargs["options"]["verify_aud"] is False
        assert set(kwargs["options"]["require"]) >= {"exp", "iss", "sub", "token_use"}

    def test_jwks_url_uses_configured_pool(self, mock_settings, mock_jwks):
        with patch(PATCH_JWT_DECODE, return_value=_access_token_payload()):
            verify_token(_bearer())
        mock_jwks.assert_called_once_with(EXPECTED_JWKS_URL)


class TestVerifyTokenClientIdValidation:
    def test_access_token_with_wrong_client_id_rejected(self, mock_settings, mock_jwks):
        payload = _access_token_payload(client_id="some-other-app-client")
        with patch(PATCH_JWT_DECODE, return_value=payload):
            with pytest.raises(HTTPException) as exc_info:
                verify_token(_bearer())
        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert exc_info.value.detail == "Could not validate credentials"

    def test_access_token_missing_client_id_rejected(self, mock_settings, mock_jwks):
        payload = _access_token_payload()
        del payload["client_id"]
        with patch(PATCH_JWT_DECODE, return_value=payload):
            with pytest.raises(HTTPException) as exc_info:
                verify_token(_bearer())
        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED

    def test_id_token_rejected(self, mock_settings, mock_jwks):
        # ID tokens are no longer accepted — flip-ui sends access tokens only.
        # A token with `token_use="id"` (even one that is otherwise valid and
        # bound to our app client via `aud`) must 401 so a UI-issued identity
        # token cannot be replayed against the API as authorisation.
        payload = {
            "sub": str(uuid4()),
            "aud": TEST_APP_CLIENT_ID,
            "iss": EXPECTED_ISSUER,
            "token_use": "id",
            "cognito:username": "user@example.com",
            "exp": 9999999999,
        }
        with patch(PATCH_JWT_DECODE, return_value=payload):
            with pytest.raises(HTTPException) as exc_info:
                verify_token(_bearer())
        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED

    def test_unknown_token_use_rejected(self, mock_settings, mock_jwks):
        payload = _access_token_payload()
        payload["token_use"] = "refresh"
        with patch(PATCH_JWT_DECODE, return_value=payload):
            with pytest.raises(HTTPException) as exc_info:
                verify_token(_bearer())
        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED


class TestVerifyTokenJwtErrors:
    def test_expired_token_returns_specific_message(self, mock_settings, mock_jwks):
        with patch(PATCH_JWT_DECODE, side_effect=jwt.ExpiredSignatureError):
            with pytest.raises(HTTPException) as exc_info:
                verify_token(_bearer())
        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert exc_info.value.detail == "Token has expired"

    def test_invalid_issuer_returns_401(self, mock_settings, mock_jwks):
        with patch(PATCH_JWT_DECODE, side_effect=jwt.InvalidIssuerError("bad issuer")):
            with pytest.raises(HTTPException) as exc_info:
                verify_token(_bearer())
        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert exc_info.value.detail == "Could not validate credentials"

    def test_invalid_signature_returns_401(self, mock_settings, mock_jwks):
        with patch(PATCH_JWT_DECODE, side_effect=jwt.InvalidSignatureError):
            with pytest.raises(HTTPException) as exc_info:
                verify_token(_bearer())
        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED

    def test_missing_required_claim_returns_401(self, mock_settings, mock_jwks):
        with patch(PATCH_JWT_DECODE, side_effect=jwt.MissingRequiredClaimError("exp")):
            with pytest.raises(HTTPException) as exc_info:
                verify_token(_bearer())
        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED


class TestVerifyTokenSubject:
    def test_non_uuid_sub_rejected(self, mock_settings, mock_jwks):
        payload = _access_token_payload(sub="not-a-uuid")
        with patch(PATCH_JWT_DECODE, return_value=payload):
            with pytest.raises(HTTPException) as exc_info:
                verify_token(_bearer())
        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert exc_info.value.detail == "Invalid user identifier format"


class TestVerifyTokenUnexpectedErrors:
    def test_jwks_fetch_failure_returns_500(self, mock_settings):
        with patch(PATCH_PYJWK_CLIENT, side_effect=RuntimeError("network down")):
            with pytest.raises(HTTPException) as exc_info:
                verify_token(_bearer())
        assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
