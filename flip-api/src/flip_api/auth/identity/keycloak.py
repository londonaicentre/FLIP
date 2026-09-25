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

"""Keycloak as the identity provider (local development, ``AUTH_BACKEND=keycloak``; FLIP#919).

Talks to the realm's Admin REST API as a confidential service-account client
(``KEYCLOAK_ADMIN_CLIENT_ID``) over the docker network (``KEYCLOAK_URL``).
The realm itself is ``deploy/keycloak/flip-realm.json``. Two module-level
helpers, :func:`password_grant` and :func:`refresh_grant`, give scripts (the
e2e smoke, the demo recorder) a user token the way the UI obtains one.
"""

import threading
import time
from typing import Any
from uuid import UUID

import httpx
from pydantic import TypeAdapter, ValidationError
from pydantic.networks import EmailStr

from flip_api.auth.identity.base import IdentityProvider
from flip_api.auth.identity.errors import (
    IdentityProviderError,
    IdentityProviderUnavailable,
    InvalidIdentifierError,
    UserAlreadyExistsError,
    UserNotFoundError,
)
from flip_api.config import DevSettings, ProdSettings
from flip_api.domain.schemas.users import CognitoUser
from flip_api.utils.cors import normalise_origins
from flip_api.utils.logger import logger

_EMAIL_VALIDATOR: TypeAdapter[str] = TypeAdapter(EmailStr)

# Refresh the admin token this long before Keycloak says it expires, so a
# request never goes out with a token that dies in flight.
_TOKEN_SKEW_SECONDS = 30.0
_PAGE_SIZE = 100
# How long the set-password link in an invitation stays valid (5 days,
# matching Cognito's temporary-password lifetime).
_INVITE_LIFESPAN_SECONDS = 5 * 24 * 3600


def _now() -> float:
    """Monotonic clock behind the admin-token expiry, patchable without touching httpx's own clock."""
    return time.monotonic()


def _token_endpoint(base_url: str, realm: str) -> str:
    return f"{base_url.rstrip('/')}/realms/{realm}/protocol/openid-connect/token"


def _grant(url: str, form: dict[str, str], transport: httpx.BaseTransport | None) -> dict[str, Any]:
    with httpx.Client(timeout=10, transport=transport) as http:
        try:
            response = http.post(url, data=form)
        except httpx.HTTPError as e:
            raise IdentityProviderUnavailable(f"Keycloak is not reachable at {url}") from e
    if response.status_code != 200:
        body = _json_or_empty(response)
        reason = body.get("error_description") or body.get("error") or f"HTTP {response.status_code}"
        raise IdentityProviderError(reason)
    result: dict[str, Any] = response.json()
    return result


def password_grant(
    public_url: str,
    realm: str,
    client_id: str,
    username: str,
    password: str,
    *,
    totp: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """Sign in as ``username`` with the OIDC password grant and return the token response.

    This is what the UI's Keycloak provider does; scripts use it to obtain a
    bearer token for the hub. ``public_url`` must be the browser-facing URL
    so the token's ``iss`` matches what flip-api verifies.
    """
    form = {
        "grant_type": "password",
        "client_id": client_id,
        "username": username,
        "password": password,
        "scope": "openid",
    }
    if totp:
        form["totp"] = totp
    return _grant(_token_endpoint(public_url, realm), form, transport)


def refresh_grant(
    public_url: str,
    realm: str,
    client_id: str,
    refresh_token: str,
    *,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """Exchange a refresh token for a new token response."""
    form = {"grant_type": "refresh_token", "client_id": client_id, "refresh_token": refresh_token}
    return _grant(_token_endpoint(public_url, realm), form, transport)


def _json_or_empty(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _safe_email(email: str) -> str:
    try:
        return _EMAIL_VALIDATOR.validate_python(email)
    except ValidationError as exc:
        raise InvalidIdentifierError("Invalid email address format") from exc


def _safe_uuid(user_id: str | UUID) -> str:
    if isinstance(user_id, UUID):
        return str(user_id)
    try:
        return str(UUID(user_id))
    except (ValueError, AttributeError, TypeError) as exc:
        raise InvalidIdentifierError("Invalid user ID format") from exc


class KeycloakIdentityProvider(IdentityProvider):
    """The users of one Keycloak realm, administered through a service-account client."""

    backend = "keycloak"

    # A Keycloak that just passed its healthcheck can still be a few seconds
    # from answering token requests; the boot seed waits it out rather than
    # failing the whole seed on the first connection refused.
    CONNECT_RETRIES = 10
    CONNECT_RETRY_SECONDS = 3.0

    def __init__(self, settings: DevSettings | ProdSettings, transport: httpx.BaseTransport | None = None) -> None:
        if settings.KEYCLOAK_URL is None or settings.KEYCLOAK_ADMIN_CLIENT_SECRET is None:
            raise ValueError("AUTH_BACKEND=keycloak requires KEYCLOAK_URL and KEYCLOAK_ADMIN_CLIENT_SECRET")
        self._base_url = settings.KEYCLOAK_URL.rstrip("/")
        self._realm = settings.KEYCLOAK_REALM
        self._ui_client_id = settings.KEYCLOAK_CLIENT_ID
        self._admin_client_id = settings.KEYCLOAK_ADMIN_CLIENT_ID
        self._admin_client_secret = settings.KEYCLOAK_ADMIN_CLIENT_SECRET.get_secret_value()
        # Only a development hub may stand in for the missing mail server by
        # handing a new user the shared dev password as a temporary one (see
        # create_user). ProdSettings cannot select this backend at all today;
        # None is what a future non-dev Keycloak deployment inherits.
        self._dev_temporary_password: str | None = None
        if settings.ENV == "development" and settings.ADMIN_USER_PASSWORD is not None:
            self._dev_temporary_password = settings.ADMIN_USER_PASSWORD.get_secret_value()
        self._http = httpx.Client(base_url=self._base_url, timeout=10, transport=transport)
        self._admin_path = f"/admin/realms/{self._realm}"
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = threading.Lock()

    # --- transport -------------------------------------------------------------------------

    def _send(self, request: httpx.Request) -> httpx.Response:
        """Send ``request``, waiting out a Keycloak that is still booting."""
        for attempt in range(self.CONNECT_RETRIES + 1):
            try:
                return self._http.send(request)
            except httpx.TransportError as e:
                if attempt == self.CONNECT_RETRIES:
                    raise IdentityProviderUnavailable(f"Keycloak is not reachable at {self._base_url}") from e
                logger.warning(
                    f"Keycloak not reachable ({e.__class__.__name__}); retrying in {self.CONNECT_RETRY_SECONDS}s"
                )
                time.sleep(self.CONNECT_RETRY_SECONDS)
        raise AssertionError("unreachable")

    def _fetch_admin_token(self) -> None:
        request = self._http.build_request(
            "POST",
            f"/realms/{self._realm}/protocol/openid-connect/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self._admin_client_id,
                "client_secret": self._admin_client_secret,
            },
        )
        response = self._send(request)
        if response.status_code >= 500:
            raise IdentityProviderUnavailable(f"Keycloak token endpoint answered HTTP {response.status_code}")
        if response.status_code != 200:
            logger.error(f"Keycloak refused the admin client credentials (HTTP {response.status_code})")
            raise IdentityProviderError("Keycloak rejected the admin client credentials")
        body = response.json()
        self._token = body["access_token"]
        self._token_expires_at = _now() + float(body.get("expires_in", 60)) - _TOKEN_SKEW_SECONDS

    def _admin_token(self, *, force: bool = False) -> str:
        with self._token_lock:
            if force or self._token is None or _now() >= self._token_expires_at:
                self._fetch_admin_token()
            assert self._token is not None
            return self._token

    def _request(
        self, method: str, path: str, *, tolerate_server_error: bool = False, **kwargs: Any
    ) -> httpx.Response:
        """Call the admin API; refresh the token once on 401; 5xx is a provider error.

        4xx responses other than 401 are returned for the caller to interpret
        (404 = no such user, 409 = already exists). ``tolerate_server_error``
        returns a 5xx too, for the one call where Keycloak answers 500 to mean
        "no mail server configured" rather than "broken".
        """
        token = self._admin_token()
        for retry in (False, True):
            request = self._http.build_request(
                method, f"{self._admin_path}/{path}", headers={"Authorization": f"Bearer {token}"}, **kwargs
            )
            response = self._send(request)
            if response.status_code == 401 and not retry:
                token = self._admin_token(force=True)
                continue
            if response.status_code == 401:
                raise IdentityProviderError("Keycloak rejected the admin client credentials")
            if response.status_code >= 500 and not tolerate_server_error:
                logger.error(f"Keycloak admin API {method} {path} answered HTTP {response.status_code}")
                raise IdentityProviderError("Keycloak admin request failed")
            return response
        raise AssertionError("unreachable")

    # --- directory -------------------------------------------------------------------------

    @staticmethod
    def _user_from(rep: dict[str, Any]) -> CognitoUser | None:
        email = rep.get("email")
        if not email:
            # Service accounts have no email and are not FLIP users.
            return None
        return CognitoUser(id=UUID(rep["id"]), email=email, is_disabled=not rep.get("enabled", True))  # type: ignore[call-arg]

    def _users(self, **params: Any) -> list[CognitoUser]:
        response = self._request("GET", "users", params={"briefRepresentation": "true", **params})
        if response.status_code != 200:
            raise IdentityProviderError("Failed to list users")
        return [user for rep in response.json() if (user := self._user_from(rep)) is not None]

    def list_users(self) -> list[CognitoUser]:
        users: list[CognitoUser] = []
        first = 0
        while True:
            response = self._request(
                "GET", "users", params={"briefRepresentation": "true", "first": first, "max": _PAGE_SIZE}
            )
            if response.status_code != 200:
                raise IdentityProviderError("Failed to list users")
            page = response.json()
            users.extend(user for rep in page if (user := self._user_from(rep)) is not None)
            if len(page) < _PAGE_SIZE:
                return users
            first += _PAGE_SIZE

    def get_user(self, *, user_id: UUID | str | None = None, email: str | None = None) -> CognitoUser:
        if not email and not user_id:
            raise InvalidIdentifierError("No user email address or ID provided")
        if email:
            matches = self._users(email=_safe_email(email), exact="true")
            if not matches:
                raise UserNotFoundError(f"User with email: {email} or ID: {user_id} is not registered.")
            return matches[0]
        assert user_id is not None
        response = self._request("GET", f"users/{_safe_uuid(user_id)}")
        if response.status_code == 404:
            raise UserNotFoundError(f"User with email: {email} or ID: {user_id} is not registered.")
        if response.status_code != 200:
            raise IdentityProviderError("Failed to get user")
        user = self._user_from(response.json())
        if user is None:
            raise UserNotFoundError(f"User with email: {email} or ID: {user_id} is not registered.")
        return user

    def _user_id_for(self, username: str) -> str:
        response = self._request("GET", "users", params={"username": username, "exact": "true"})
        if response.status_code != 200:
            raise IdentityProviderError("Failed to look up user")
        matches = response.json()
        if not matches:
            raise UserNotFoundError(f"User {username} is not registered.")
        return str(matches[0]["id"])

    def set_enabled(self, username: str, enabled: bool) -> None:
        user_id = self._user_id_for(username)
        response = self._request("PUT", f"users/{user_id}", json={"enabled": enabled})
        if response.status_code >= 400:
            raise IdentityProviderError("Failed to update user")
        logger.debug(f"User {username} {'enabled' if enabled else 'disabled'}")

    def create_user(self, email: str, *, suppress_invite: bool = False) -> UUID:
        email = _safe_email(email)
        response = self._request(
            "POST",
            "users",
            json={
                "username": email,
                "email": email,
                "enabled": True,
                "emailVerified": True,
                "requiredActions": ["UPDATE_PASSWORD"],
            },
        )
        if response.status_code == 409:
            raise UserAlreadyExistsError(f"User with email {email} already exists")
        if response.status_code != 201:
            logger.error(f"Keycloak refused to create a user (HTTP {response.status_code})")
            raise IdentityProviderError("Failed to create user")
        user_id = response.headers.get("Location", "").rstrip("/").rsplit("/", 1)[-1]
        if not user_id:
            raise IdentityProviderError("User created but could not get user ID")
        logger.info("User has been created successfully")
        if not suppress_invite:
            self._invite(user_id, email)
        return UUID(user_id)

    def _invite(self, user_id: str, email: str) -> None:
        """Ask Keycloak to email a set-password link; in dev, stand in for the missing SMTP."""
        response = self._request(
            "PUT",
            f"users/{user_id}/execute-actions-email",
            tolerate_server_error=True,
            params={"client_id": self._ui_client_id, "lifespan": _INVITE_LIFESPAN_SECONDS},
            json=["UPDATE_PASSWORD"],
        )
        if response.status_code < 400:
            return
        if self._dev_temporary_password is None:
            logger.warning(
                f"Keycloak could not email the set-password link for {email} (HTTP {response.status_code}); "
                "set a temporary password in the Keycloak admin console."
            )
            return
        # Development has no mail server: stand in for the invitation with the
        # shared dev password (ADMIN_USER_PASSWORD, already in the env file) as
        # a temporary one, so nothing secret has to be logged. Keycloak asks
        # for a new password at the first sign-in.
        reset = self._request(
            "PUT",
            f"users/{user_id}/reset-password",
            json={"type": "password", "value": self._dev_temporary_password, "temporary": True},
        )
        if reset.status_code >= 400:
            raise IdentityProviderError("User created but the temporary password could not be set")
        logger.warning(
            f"[dev] Keycloak has no SMTP: {email} was given the shared dev password (ADMIN_USER_PASSWORD) as a "
            "temporary one — sign in once, Keycloak will ask for a new password."
        )

    def delete_user(self, username: str) -> None:
        user_id = self._user_id_for(username)
        response = self._request("DELETE", f"users/{user_id}")
        if response.status_code == 404:
            raise UserNotFoundError(f"User {username} is not registered.")
        if response.status_code >= 400:
            raise IdentityProviderError("Failed to delete user")
        logger.info(f"Successfully deleted user: {username}")

    # --- MFA -------------------------------------------------------------------------------

    def _otp_credentials(self, user_id: str) -> list[dict[str, Any]]:
        response = self._request("GET", f"users/{user_id}/credentials")
        if response.status_code != 200:
            raise IdentityProviderError("Failed to fetch MFA state")
        return [cred for cred in response.json() if cred.get("type") == "otp"]

    def _fetch_mfa_enabled(self, username: str) -> bool:
        return bool(self._otp_credentials(self._user_id_for(username)))

    def _reset_mfa(self, username: str) -> None:
        user_id = self._user_id_for(username)
        for cred in self._otp_credentials(user_id):
            response = self._request("DELETE", f"users/{user_id}/credentials/{cred['id']}")
            if response.status_code >= 400:
                raise IdentityProviderError("Failed to reset user MFA")
        response = self._request("POST", f"users/{user_id}/logout")
        if response.status_code >= 400:
            raise IdentityProviderError("Failed to reset user MFA")
        logger.info(f"Successfully reset MFA and revoked sessions for user: {username}")

    # --- origins ---------------------------------------------------------------------------

    def allowed_origins(self) -> list[str]:
        """The UI client's redirect URIs, normalised to origins (the realm file is the one source)."""
        response = self._request("GET", "clients", params={"clientId": self._ui_client_id})
        if response.status_code != 200 or not response.json():
            raise IdentityProviderError(f"Keycloak client {self._ui_client_id} not found in realm {self._realm}")
        redirect_uris: list[str] = response.json()[0].get("redirectUris", []) or []
        return normalise_origins(uri.rstrip("*").rstrip("/") for uri in redirect_uris)

    # --- operator tooling ------------------------------------------------------------------

    def set_password(self, email: str, password: str) -> None:
        user_id = self._user_id_for(email)
        response = self._request(
            "PUT", f"users/{user_id}/reset-password", json={"type": "password", "value": password, "temporary": False}
        )
        if response.status_code >= 400:
            raise IdentityProviderError("Failed to set password")

    def describe_target(self) -> str:
        return f"Keycloak realm {self._realm} at {self._base_url}"
