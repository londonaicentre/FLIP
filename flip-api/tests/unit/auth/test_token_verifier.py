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

"""The generic OIDC access-token verifier behind ``verify_token`` (#919).

Tokens are signed here with a real RSA key and verified against a JWKS that
``PyJWKClient`` is fed directly, so every case exercises PyJWT's actual
signature, issuer, audience and expiry checks rather than a mocked
``jwt.decode``. Only the per-backend claim rules differ between Cognito and
Keycloak; both go through the same code path.
"""

import time
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException, status
from jwt import PyJWKClient

from flip_api.auth import token_verifier
from flip_api.auth.token_verifier import (
    VerifiedIdentity,
    cognito_rules,
    keycloak_rules,
    token_rules_for,
    verify_access_token,
)

REGION = "eu-west-2"
POOL_ID = "eu-west-2_TESTPOOL"
APP_CLIENT_ID = "test-app-client-id"
COGNITO_ISSUER = f"https://cognito-idp.{REGION}.amazonaws.com/{POOL_ID}"

KEYCLOAK_URL = "http://keycloak:8080"
KEYCLOAK_PUBLIC_URL = "http://localhost:8180"
KEYCLOAK_ISSUER = f"{KEYCLOAK_PUBLIC_URL}/realms/flip"
KEYCLOAK_JWKS_URL = f"{KEYCLOAK_URL}/realms/flip/protocol/openid-connect/certs"

KID = "test-key-1"


@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def private_pem(rsa_key) -> bytes:
    return rsa_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


@pytest.fixture(scope="module")
def jwks(rsa_key) -> dict[str, Any]:
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(rsa_key.public_key(), as_dict=True)
    jwk.update({"kid": KID, "alg": "RS256", "use": "sig"})
    return {"keys": [jwk]}


@pytest.fixture(autouse=True)
def _fresh_jwks_cache():
    token_verifier._jwks_client.cache_clear()
    yield
    token_verifier._jwks_client.cache_clear()


@pytest.fixture
def fetch_jwks(jwks):
    """Serve the module JWKS to every PyJWKClient instead of hitting the network."""
    with patch.object(PyJWKClient, "fetch_data", return_value=jwks) as fetch:
        yield fetch


def _settings(backend: str) -> MagicMock:
    settings = MagicMock()
    settings.AUTH_BACKEND = backend
    settings.AWS_REGION = REGION
    settings.AWS_COGNITO_USER_POOL_ID = POOL_ID
    settings.AWS_COGNITO_APP_CLIENT_ID = APP_CLIENT_ID
    settings.KEYCLOAK_URL = KEYCLOAK_URL
    settings.KEYCLOAK_PUBLIC_URL = KEYCLOAK_PUBLIC_URL
    settings.KEYCLOAK_REALM = "flip"
    settings.KEYCLOAK_AUDIENCE = "flip-api"
    return settings


@pytest.fixture
def cognito_settings():
    with patch("flip_api.auth.token_verifier.get_settings", return_value=_settings("cognito")):
        yield


@pytest.fixture
def keycloak_settings():
    with patch("flip_api.auth.token_verifier.get_settings", return_value=_settings("keycloak")):
        yield


def _sign(private_pem: bytes, claims: dict[str, Any], kid: str = KID) -> str:
    return jwt.encode(claims, private_pem, algorithm="RS256", headers={"kid": kid})


def _cognito_claims(**overrides: Any) -> dict[str, Any]:
    claims: dict[str, Any] = {
        "sub": str(uuid4()),
        "iss": COGNITO_ISSUER,
        "client_id": APP_CLIENT_ID,
        "token_use": "access",
        "username": "user@example.com",
        "exp": int(time.time()) + 300,
    }
    claims.update(overrides)
    return claims


def _keycloak_claims(**overrides: Any) -> dict[str, Any]:
    claims: dict[str, Any] = {
        "sub": str(uuid4()),
        "iss": KEYCLOAK_ISSUER,
        "aud": ["flip-api", "account"],
        "typ": "Bearer",
        "azp": "flip-ui",
        "preferred_username": "user@example.com",
        "exp": int(time.time()) + 300,
    }
    claims.update(overrides)
    return claims


# --- rules -----------------------------------------------------------------------------


def test_cognito_rules_derive_issuer_and_jwks_from_the_pool():
    rules = cognito_rules(_settings("cognito"))
    assert rules.issuer == COGNITO_ISSUER
    assert rules.jwks_url == f"{COGNITO_ISSUER}/.well-known/jwks.json"
    # Cognito access tokens carry `client_id`, not `aud`.
    assert rules.audience is None
    assert rules.username_claim == "username"


def test_keycloak_rules_verify_the_public_issuer_but_fetch_keys_internally():
    """The browser mints tokens through the published port, flip-api reads keys over the docker network."""
    rules = keycloak_rules(_settings("keycloak"))
    assert rules.issuer == KEYCLOAK_ISSUER
    assert rules.jwks_url == KEYCLOAK_JWKS_URL
    assert rules.audience == "flip-api"
    assert rules.username_claim == "preferred_username"


def test_token_rules_for_dispatches_on_the_backend():
    assert token_rules_for(_settings("cognito")).issuer == COGNITO_ISSUER
    assert token_rules_for(_settings("keycloak")).issuer == KEYCLOAK_ISSUER


# --- cognito ---------------------------------------------------------------------------


def test_cognito_access_token_yields_the_identity(cognito_settings, fetch_jwks, private_pem):
    sub = str(uuid4())
    identity = verify_access_token(_sign(private_pem, _cognito_claims(sub=sub)))
    assert identity == VerifiedIdentity(sub=UUID(sub), username="user@example.com")


def test_cognito_id_token_is_rejected(cognito_settings, fetch_jwks, private_pem):
    """An ID token bound to the client via `aud` must not be replayable as authorisation (#344)."""
    claims = _cognito_claims(token_use="id", aud=APP_CLIENT_ID)
    del claims["client_id"]
    with pytest.raises(HTTPException) as exc_info:
        verify_access_token(_sign(private_pem, claims))
    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert exc_info.value.detail == "Could not validate credentials"


def test_cognito_token_for_another_app_client_is_rejected(cognito_settings, fetch_jwks, private_pem):
    with pytest.raises(HTTPException) as exc_info:
        verify_access_token(_sign(private_pem, _cognito_claims(client_id="someone-else")))
    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED


def test_cognito_token_without_username_is_rejected(cognito_settings, fetch_jwks, private_pem):
    """The MFA gate keys on the username claim, so a token without it cannot be admitted."""
    claims = _cognito_claims()
    del claims["username"]
    with pytest.raises(HTTPException) as exc_info:
        verify_access_token(_sign(private_pem, claims))
    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert "username" in exc_info.value.detail.lower()


# --- keycloak --------------------------------------------------------------------------


def test_keycloak_access_token_yields_the_identity(keycloak_settings, fetch_jwks, private_pem):
    sub = str(uuid4())
    identity = verify_access_token(_sign(private_pem, _keycloak_claims(sub=sub)))
    assert identity == VerifiedIdentity(sub=UUID(sub), username="user@example.com")


def test_keycloak_id_token_is_rejected(keycloak_settings, fetch_jwks, private_pem):
    """Keycloak marks token type in `typ`; only bearer access tokens are authorisation."""
    with pytest.raises(HTTPException) as exc_info:
        verify_access_token(_sign(private_pem, _keycloak_claims(typ="ID")))
    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED


def test_keycloak_token_without_the_api_audience_is_rejected(keycloak_settings, fetch_jwks, private_pem):
    with pytest.raises(HTTPException) as exc_info:
        verify_access_token(_sign(private_pem, _keycloak_claims(aud="account")))
    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED


def test_keycloak_token_minted_through_the_internal_hostname_is_rejected(keycloak_settings, fetch_jwks, private_pem):
    """`iss` must be the public issuer; a token stamped with the docker hostname is a misconfiguration, not a login."""
    with pytest.raises(HTTPException) as exc_info:
        verify_access_token(_sign(private_pem, _keycloak_claims(iss=f"{KEYCLOAK_URL}/realms/flip")))
    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED


# --- shared error mapping ---------------------------------------------------------------


def test_expired_token_returns_the_specific_message(cognito_settings, fetch_jwks, private_pem):
    with pytest.raises(HTTPException) as exc_info:
        verify_access_token(_sign(private_pem, _cognito_claims(exp=int(time.time()) - 60)))
    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert exc_info.value.detail == "Token has expired"


def test_token_signed_by_an_unknown_key_is_rejected(cognito_settings, fetch_jwks, private_pem):
    with pytest.raises(HTTPException) as exc_info:
        verify_access_token(_sign(private_pem, _cognito_claims(), kid="rotated-away"))
    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED


def test_non_uuid_subject_is_rejected(cognito_settings, fetch_jwks, private_pem):
    with pytest.raises(HTTPException) as exc_info:
        verify_access_token(_sign(private_pem, _cognito_claims(sub="not-a-uuid")))
    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert exc_info.value.detail == "Invalid user identifier format"


def test_jwks_fetch_failure_returns_500_without_leaking_the_cause(cognito_settings, private_pem):
    with patch.object(PyJWKClient, "fetch_data", side_effect=RuntimeError("connection refused")):
        with pytest.raises(HTTPException) as exc_info:
            verify_access_token(_sign(private_pem, _cognito_claims()))
    assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert "connection refused" not in exc_info.value.detail


def test_jwks_client_is_built_once_per_jwks_url(cognito_settings, fetch_jwks, private_pem):
    """Regression for the per-request PyJWKClient: two verifications, one JWKS fetch."""
    verify_access_token(_sign(private_pem, _cognito_claims()))
    verify_access_token(_sign(private_pem, _cognito_claims()))
    assert fetch_jwks.call_count == 1
