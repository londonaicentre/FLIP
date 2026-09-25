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

"""Generic OIDC access-token verification, parameterised per identity provider (FLIP#919).

Every provider FLIP can authenticate against issues RS256 JWTs and publishes the
signing keys as a JWKS; what differs is the issuer, where the keys live, and a
handful of claim conventions (Cognito binds a token to the app client with
``client_id`` and marks its kind with ``token_use``; Keycloak uses ``aud`` and
``typ``). Those differences are captured in a :class:`TokenRules` bundle chosen
by ``Settings.AUTH_BACKEND``; the verification itself is one code path, so a
new provider is a new rules builder rather than a second verifier.
"""

from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any
from uuid import UUID

import jwt
from fastapi import HTTPException, status
from jwt import PyJWKClient

from flip_api.config import DevSettings, ProdSettings, get_settings
from flip_api.utils.logger import logger


@dataclass(frozen=True)
class VerifiedIdentity:
    """What a verified access token says about its bearer.

    Attributes:
        sub: The provider's stable user id; FLIP keys profiles and roles on it.
        username: The provider's principal name (the email in both FLIP realms),
            which the MFA gate hands back to the identity provider.
    """

    sub: UUID
    username: str


@dataclass(frozen=True)
class TokenRules:
    """The provider-specific inputs to :func:`decode_access_token`.

    Attributes:
        issuer: Exact ``iss`` a token must carry.
        jwks_url: Where the signing keys are fetched from. May differ in host
            from ``issuer`` (Keycloak: public issuer, docker-network JWKS).
        audience: Value ``aud`` must contain, or ``None`` when the provider
            binds tokens to a client some other way (Cognito's ``client_id``).
        required_claims: Claims PyJWT must find present.
        username_claim: Claim holding the principal name.
        extra_checks: Provider-specific assertions over the decoded payload;
            raise ``jwt.InvalidTokenError`` to reject.
    """

    issuer: str
    jwks_url: str
    audience: str | None
    required_claims: tuple[str, ...]
    username_claim: str
    extra_checks: Callable[[dict[str, Any]], None]


def _cognito_checks(settings: DevSettings | ProdSettings) -> Callable[[dict[str, Any]], None]:
    app_client_id = settings.AWS_COGNITO_APP_CLIENT_ID

    def check(payload: dict[str, Any]) -> None:
        # ID tokens (and any other token_use) are rejected — see issue #344.
        token_use = payload.get("token_use")
        if token_use != "access":
            raise jwt.InvalidTokenError(f"Unsupported token_use: {token_use!r}")
        if payload.get("client_id") != app_client_id:
            raise jwt.InvalidTokenError("Invalid client_id")

    return check


def _keycloak_checks(payload: dict[str, Any]) -> None:
    # Keycloak stamps access tokens "Bearer"; ID tokens carry "ID" and refresh
    # tokens "Refresh". Only bearer tokens are authorisation.
    typ = payload.get("typ")
    if typ != "Bearer":
        raise jwt.InvalidTokenError(f"Unsupported typ: {typ!r}")


def cognito_rules(settings: DevSettings | ProdSettings) -> TokenRules:
    """Rules for AWS Cognito user-pool access tokens (AWS's documented verification steps)."""
    issuer = f"https://cognito-idp.{settings.AWS_REGION}.amazonaws.com/{settings.AWS_COGNITO_USER_POOL_ID}"
    return TokenRules(
        issuer=issuer,
        jwks_url=f"{issuer}/.well-known/jwks.json",
        # Cognito access tokens bind to the app client via `client_id`, not `aud`.
        audience=None,
        required_claims=("exp", "iss", "sub", "token_use"),
        username_claim="username",
        extra_checks=_cognito_checks(settings),
    )


def keycloak_rules(settings: DevSettings | ProdSettings) -> TokenRules:
    """Rules for the Keycloak realm's access tokens.

    The issuer is the public URL (what the browser signs in through, pinned
    by ``KC_HOSTNAME``), while the keys are fetched over the docker network —
    deliberately not via OIDC discovery, which would advertise the public
    ``jwks_uri`` that flip-api cannot reach from inside the compose network.
    """
    realm = settings.KEYCLOAK_REALM
    return TokenRules(
        issuer=f"{settings.KEYCLOAK_PUBLIC_URL}/realms/{realm}",
        jwks_url=f"{settings.KEYCLOAK_URL}/realms/{realm}/protocol/openid-connect/certs",
        audience=settings.KEYCLOAK_AUDIENCE,
        required_claims=("exp", "iss", "sub", "typ", "aud"),
        username_claim="preferred_username",
        extra_checks=_keycloak_checks,
    )


def token_rules_for(settings: DevSettings | ProdSettings) -> TokenRules:
    """Select the rules bundle for ``settings.AUTH_BACKEND``."""
    if settings.AUTH_BACKEND == "keycloak":
        return keycloak_rules(settings)
    return cognito_rules(settings)


@lru_cache(maxsize=4)
def _jwks_client(jwks_url: str) -> PyJWKClient:
    """One JWKS client per URL for the life of the process.

    ``PyJWKClient`` caches the fetched key set and refetches on an unknown
    ``kid``, so building it once (rather than per request, as the original
    verifier did) removes a network round-trip from every authenticated call
    while still following key rotation.
    """
    return PyJWKClient(jwks_url, cache_keys=True, max_cached_keys=16, lifespan=300)


def decode_access_token(token: str, rules: TokenRules | None = None) -> dict[str, Any]:
    """Verify ``token`` against ``rules`` (default: the configured backend's) and return its claims.

    Raises:
        jwt.InvalidTokenError: On any signature, expiry, issuer, audience or
            claim-rule failure.
    """
    rules = rules or token_rules_for(get_settings())
    signing_key = _jwks_client(rules.jwks_url).get_signing_key_from_jwt(token)
    payload: dict[str, Any] = jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        issuer=rules.issuer,
        audience=rules.audience,
        options={
            "verify_aud": rules.audience is not None,
            "require": list(rules.required_claims),
        },
    )
    rules.extra_checks(payload)
    return payload


def _extract_user_id(payload: dict[str, Any]) -> UUID:
    """Return the ``sub`` claim as a UUID, raising 401 on failure."""
    user_id_str = payload.get("sub")
    if not user_id_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing user identifier",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return UUID(user_id_str)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid user identifier format",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _extract_username(payload: dict[str, Any], claim: str) -> str:
    """Return the principal-name claim, raising 401 if it is missing."""
    username = payload.get(claim)
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing username claim",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return str(username)


def verify_access_token(token: str) -> VerifiedIdentity:
    """Verify a bearer token for the configured backend and return who it identifies.

    Args:
        token: The raw bearer token.

    Returns:
        VerifiedIdentity: The ``sub`` and principal name from the verified claims.

    Raises:
        HTTPException: 401 if the token is invalid, expired, of the wrong kind
            or missing the claims FLIP relies on; 500 if verification itself
            failed (e.g. the JWKS could not be fetched).
    """
    rules = token_rules_for(get_settings())
    try:
        payload = decode_access_token(token, rules)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.exceptions.PyJWKClientConnectionError as e:
        logger.error(f"Could not fetch the signing keys from {rules.jwks_url}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error during authentication",
        )
    except (jwt.InvalidTokenError, jwt.exceptions.PyJWKClientError) as e:
        # PyJWKClientError covers a `kid` the JWKS does not know: a token signed
        # by a key the provider never published, or rotated away — the bearer's
        # problem, not ours.
        logger.error(f"Token validation failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except Exception as e:
        logger.error(f"Unexpected error during token verification: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error during authentication",
        )
    return VerifiedIdentity(sub=_extract_user_id(payload), username=_extract_username(payload, rules.username_claim))
