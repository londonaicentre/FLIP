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

"""KeycloakIdentityProvider against a scripted Keycloak Admin REST API (FLIP#919).

``FakeKeycloak`` answers the handful of admin endpoints the provider uses
from an in-memory realm, served through ``httpx.MockTransport`` so the real
request building, auth headers, pagination and error handling run
end-to-end without a container. The Testcontainers round-trip in
``tests/integration`` covers the real thing.
"""

import json
from typing import Any
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr

from flip_api.auth.identity.errors import (
    IdentityProviderError,
    IdentityProviderUnavailable,
    InvalidIdentifierError,
    UserAlreadyExistsError,
    UserNotFoundError,
)
from flip_api.auth.identity.keycloak import KeycloakIdentityProvider, password_grant, refresh_grant

REALM = "flip"
ADMIN_TOKEN = "admin-token-1"


def _settings(env: str = "development") -> MagicMock:
    settings = MagicMock()
    settings.ENV = env
    settings.KEYCLOAK_URL = "http://keycloak:8080"
    settings.KEYCLOAK_PUBLIC_URL = "http://localhost:8180"
    settings.KEYCLOAK_REALM = REALM
    settings.KEYCLOAK_CLIENT_ID = "flip-ui"
    settings.KEYCLOAK_AUDIENCE = "flip-api"
    settings.KEYCLOAK_ADMIN_CLIENT_ID = "flip-api-admin"
    settings.KEYCLOAK_ADMIN_CLIENT_SECRET = SecretStr("dev-secret")
    settings.ADMIN_USER_PASSWORD = SecretStr("Shared-Dev-Pa55word!")  # pragma: allowlist secret
    return settings


class FakeKeycloak:
    """Just enough of Keycloak's admin API, backed by dicts, recording every request."""

    def __init__(self) -> None:
        self.users: dict[str, dict[str, Any]] = {}
        self.credentials: dict[str, list[dict[str, Any]]] = {}
        self.logged_out: list[str] = []
        self.action_emails: list[tuple[str, list[str], dict[str, str]]] = []
        self.password_resets: list[tuple[str, dict[str, Any]]] = []
        self.requests: list[httpx.Request] = []
        self.token_requests = 0
        self.token_expires_in = 300
        self.smtp_configured = True
        self.redirect_uris = ["http://localhost:44350/*", "http://localhost:44351/*", "https://localhost:443/*"]
        self.reject_tokens: set[str] = set()
        self.connect_failures = 0
        self.token_failures = 0

    def add_user(self, email: str, *, enabled: bool = True, otp: bool = False) -> str:
        user_id = str(uuid4())
        self.users[user_id] = {"id": user_id, "username": email, "email": email, "enabled": enabled}
        self.credentials[user_id] = [{"id": str(uuid4()), "type": "password"}]
        if otp:
            self.credentials[user_id].append({"id": str(uuid4()), "type": "otp"})
        return user_id

    def add_service_account(self) -> None:
        user_id = str(uuid4())
        self.users[user_id] = {"id": user_id, "username": "service-account-flip-api-admin", "enabled": True}

    # --- transport -------------------------------------------------------------------------

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.connect_failures:
            self.connect_failures -= 1
            raise httpx.ConnectError("connection refused", request=request)
        path = request.url.path
        if path == f"/realms/{REALM}/protocol/openid-connect/token":
            return self._token(request)
        if not path.startswith(f"/admin/realms/{REALM}/"):
            return httpx.Response(404)
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        if token != ADMIN_TOKEN or token in self.reject_tokens:
            return httpx.Response(401, json={"error": "HTTP 401 Unauthorized"})
        return self._admin(request, path.removeprefix(f"/admin/realms/{REALM}/"))

    def _token(self, request: httpx.Request) -> httpx.Response:
        self.token_requests += 1
        if self.token_failures:
            self.token_failures -= 1
            return httpx.Response(503, text="Service Unavailable")
        form = parse_qs(request.content.decode())
        if form.get("grant_type") == ["client_credentials"]:
            if form.get("client_secret") != ["dev-secret"]:
                return httpx.Response(401, json={"error": "unauthorized_client"})
            return httpx.Response(200, json={"access_token": ADMIN_TOKEN, "expires_in": self.token_expires_in})
        if form.get("grant_type") == ["password"]:
            if form.get("password") == ["correct"]:
                body = {"access_token": "user-token", "refresh_token": "refresh-1", "expires_in": 300}
                if form.get("totp"):
                    body["totp_seen"] = form["totp"][0]
                return httpx.Response(200, json=body)
            # Keycloak 26 answers a bad password with 400 invalid_grant (older releases used 401).
            return httpx.Response(400, json={"error": "invalid_grant", "error_description": "Invalid user credentials"})
        if form.get("grant_type") == ["refresh_token"]:
            if form.get("refresh_token") == ["refresh-1"]:
                return httpx.Response(200, json={"access_token": "user-token-2", "refresh_token": "refresh-2"})
            return httpx.Response(400, json={"error": "invalid_grant", "error_description": "Session not active"})
        return httpx.Response(400, json={"error": "unsupported_grant_type"})

    def _admin(self, request: httpx.Request, path: str) -> httpx.Response:  # noqa: C901 - a small router
        params = request.url.params
        if path == "users" and request.method == "GET":
            users = list(self.users.values())
            if "email" in params:
                users = [u for u in users if u.get("email") == params["email"]]
            if "username" in params:
                users = [u for u in users if u["username"] == params["username"]]
            first, maximum = int(params.get("first", 0)), int(params.get("max", 100))
            return httpx.Response(200, json=users[first : first + maximum])
        if path == "users" and request.method == "POST":
            body = json.loads(request.content)
            if any(u.get("email") == body["email"] for u in self.users.values()):
                return httpx.Response(409, json={"errorMessage": "User exists with same email"})
            user_id = str(uuid4())
            self.users[user_id] = {"id": user_id, **body}
            self.credentials[user_id] = []
            return httpx.Response(201, headers={"Location": f"http://keycloak:8080/admin/realms/{REALM}/users/{user_id}"})
        if path == "clients" and request.method == "GET":
            if params.get("clientId") != "flip-ui":
                return httpx.Response(200, json=[])
            return httpx.Response(200, json=[{"id": "c1", "clientId": "flip-ui", "redirectUris": self.redirect_uris}])
        parts = path.split("/")
        if parts[0] != "users" or len(parts) < 2 or parts[1] not in self.users:
            return httpx.Response(404, json={"error": "User not found"})
        user_id = parts[1]
        rest = parts[2:]
        if not rest and request.method == "GET":
            return httpx.Response(200, json=self.users[user_id])
        if not rest and request.method == "PUT":
            self.users[user_id].update(json.loads(request.content))
            return httpx.Response(204)
        if not rest and request.method == "DELETE":
            del self.users[user_id]
            return httpx.Response(204)
        if rest == ["credentials"] and request.method == "GET":
            return httpx.Response(200, json=self.credentials[user_id])
        if len(rest) == 2 and rest[0] == "credentials" and request.method == "DELETE":
            self.credentials[user_id] = [c for c in self.credentials[user_id] if c["id"] != rest[1]]
            return httpx.Response(204)
        if rest == ["logout"] and request.method == "PUT" or rest == ["logout"] and request.method == "POST":
            self.logged_out.append(user_id)
            return httpx.Response(204)
        if rest == ["execute-actions-email"] and request.method == "PUT":
            if not self.smtp_configured:
                return httpx.Response(500, json={"errorMessage": "Failed to send execute actions email"})
            self.action_emails.append((user_id, json.loads(request.content), dict(params)))
            return httpx.Response(204)
        if rest == ["reset-password"] and request.method == "PUT":
            self.password_resets.append((user_id, json.loads(request.content)))
            return httpx.Response(204)
        return httpx.Response(404)


@pytest.fixture
def keycloak() -> FakeKeycloak:
    return FakeKeycloak()


@pytest.fixture
def provider(keycloak) -> KeycloakIdentityProvider:
    return KeycloakIdentityProvider(_settings(), transport=httpx.MockTransport(keycloak.handler))


def _admin_calls(keycloak: FakeKeycloak) -> list[httpx.Request]:
    return [r for r in keycloak.requests if r.url.path.startswith("/admin/")]


def _provider_where(
    keycloak: FakeKeycloak, method: str, path_suffix: str, response: httpx.Response
) -> KeycloakIdentityProvider:
    """A provider whose admin ``method`` on a path ending ``path_suffix`` gets ``response``; the rest is the fake."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == method and path.startswith("/admin/") and path.endswith(path_suffix):
            keycloak.requests.append(request)
            return response
        return keycloak.handler(request)

    return KeycloakIdentityProvider(_settings(), transport=httpx.MockTransport(handler))


# --- construction and the admin token ----------------------------------------------------------


def test_backend_and_target(provider):
    assert provider.backend == "keycloak"
    assert REALM in provider.describe_target()
    assert "http://keycloak:8080" in provider.describe_target()


@pytest.mark.parametrize("missing", ["KEYCLOAK_URL", "KEYCLOAK_ADMIN_CLIENT_SECRET"])
def test_construction_without_the_admin_coordinates_is_refused(missing):
    settings = _settings()
    setattr(settings, missing, None)
    with pytest.raises(ValueError, match="KEYCLOAK_URL and KEYCLOAK_ADMIN_CLIENT_SECRET"):
        KeycloakIdentityProvider(settings)


def test_construction_makes_no_requests(keycloak, provider):
    assert keycloak.requests == []


def test_admin_token_is_fetched_once_and_reused(keycloak, provider):
    keycloak.add_user("a@example.com")
    provider.list_users()
    provider.list_users()
    assert keycloak.token_requests == 1
    assert all(r.headers["Authorization"] == f"Bearer {ADMIN_TOKEN}" for r in _admin_calls(keycloak))


def test_admin_token_is_refetched_when_it_expires(keycloak, provider):
    keycloak.token_expires_in = 40  # inside the 30 s skew after the first tick
    # Clock reads: stamp the first token (0 s), check before the second call (20 s), stamp the second token.
    with patch("flip_api.auth.identity.keycloak._now", side_effect=[0.0, 20.0, 20.0]):
        provider.list_users()
        provider.list_users()
    assert keycloak.token_requests == 2


def test_a_rejected_admin_token_is_refreshed_once_and_the_call_retried(keycloak):
    """Keycloak restarted and forgot the cached token: one 401 means refresh and retry, not fail."""
    keycloak.add_user("a@example.com")
    state = {"rejected": False}

    def restarted_once(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/admin/") and not state["rejected"]:
            state["rejected"] = True
            return httpx.Response(401, json={"error": "HTTP 401 Unauthorized"})
        return keycloak.handler(request)

    provider = KeycloakIdentityProvider(_settings(), transport=httpx.MockTransport(restarted_once))
    assert len(provider.list_users()) == 1
    assert keycloak.token_requests == 2


def test_a_persistently_rejected_admin_token_is_a_provider_error(keycloak):
    """A second 401 after a fresh token is not retried again — that would loop on a misconfigured realm."""
    keycloak.add_user("a@example.com")

    def always_rejects(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/admin/"):
            return httpx.Response(401, json={"error": "HTTP 401 Unauthorized"})
        return keycloak.handler(request)

    provider = KeycloakIdentityProvider(_settings(), transport=httpx.MockTransport(always_rejects))
    with pytest.raises(IdentityProviderError):
        provider.list_users()
    assert keycloak.token_requests == 2


def test_unreachable_keycloak_is_unavailable_after_bounded_retries(keycloak, provider):
    keycloak.connect_failures = 100
    with patch("flip_api.auth.identity.keycloak.time.sleep") as sleep:
        with pytest.raises(IdentityProviderUnavailable):
            provider.list_users()
    assert sleep.call_count == provider.CONNECT_RETRIES


def test_a_slow_boot_is_waited_out(keycloak, provider):
    """Compose's healthcheck admits flip-api slightly before the realm answers; the seed must survive it."""
    keycloak.add_user("a@example.com")
    keycloak.connect_failures = 2
    with patch("flip_api.auth.identity.keycloak.time.sleep"):
        assert len(provider.list_users()) == 1


def test_wrong_admin_secret_is_a_provider_error_not_a_retry_loop(keycloak):
    settings = _settings()
    settings.KEYCLOAK_ADMIN_CLIENT_SECRET = SecretStr("wrong")
    provider = KeycloakIdentityProvider(settings, transport=httpx.MockTransport(keycloak.handler))
    with patch("flip_api.auth.identity.keycloak.time.sleep") as sleep:
        with pytest.raises(IdentityProviderError):
            provider.list_users()
    sleep.assert_not_called()


def test_a_failing_token_endpoint_is_unavailable_not_a_credentials_error(keycloak, provider):
    """A 5xx from the token endpoint means Keycloak is sick (503 upstream), not that the secret is wrong."""
    keycloak.token_failures = 1
    with pytest.raises(IdentityProviderUnavailable, match="HTTP 503"):
        provider.list_users()
    assert _admin_calls(keycloak) == []


def test_a_server_error_from_the_admin_api_is_a_provider_error(keycloak):
    keycloak.add_user("a@example.com")
    provider = _provider_where(keycloak, "GET", "/users", httpx.Response(502, text="Bad Gateway"))
    with patch("flip_api.auth.identity.keycloak.logger") as logger:
        with pytest.raises(IdentityProviderError, match="admin request failed") as exc_info:
            provider.list_users()
    assert not isinstance(exc_info.value, IdentityProviderUnavailable)
    assert "HTTP 502" in str(logger.error.call_args)


# --- directory ---------------------------------------------------------------------------


def test_list_users_maps_the_representation_and_skips_service_accounts(keycloak, provider):
    alice = keycloak.add_user("alice@example.com")
    keycloak.add_user("bob@example.com", enabled=False)
    keycloak.add_service_account()
    users = {u.email: u for u in provider.list_users()}
    assert set(users) == {"alice@example.com", "bob@example.com"}
    assert users["alice@example.com"].id == UUID(alice)
    assert users["alice@example.com"].is_disabled is False
    assert users["bob@example.com"].is_disabled is True


def test_list_users_walks_every_page(keycloak, provider):
    for i in range(250):
        keycloak.add_user(f"user{i}@example.com")
    assert len(provider.list_users()) == 250
    pages = [r for r in _admin_calls(keycloak) if r.url.path.endswith("/users")]
    assert [int(r.url.params["first"]) for r in pages] == [0, 100, 200]
    assert all(r.url.params["briefRepresentation"] == "true" for r in pages)


def test_get_user_by_email_uses_an_exact_match(keycloak, provider):
    alice = keycloak.add_user("alice@example.com")
    keycloak.add_user("alice@example.com.evil")
    user = provider.get_user(email="alice@example.com")
    assert user.id == UUID(alice)
    request = _admin_calls(keycloak)[-1]
    assert request.url.params["email"] == "alice@example.com"
    assert request.url.params["exact"] == "true"


def test_get_user_by_id_fetches_the_user_directly(keycloak, provider):
    alice = keycloak.add_user("alice@example.com")
    user = provider.get_user(user_id=UUID(alice))
    assert user.email == "alice@example.com"
    assert _admin_calls(keycloak)[-1].url.path.endswith(f"/users/{alice}")


def test_get_user_not_found_keeps_the_wording_callers_match_on(keycloak, provider):
    missing = uuid4()
    with pytest.raises(UserNotFoundError, match="is not registered"):
        provider.get_user(user_id=missing)
    with pytest.raises(UserNotFoundError, match="is not registered"):
        provider.get_user(email="nobody@example.com")


@pytest.mark.parametrize("email", ['a"b@example.com', "not-an-email", ""])
def test_get_user_rejects_malformed_emails_before_calling_keycloak(keycloak, provider, email):
    with pytest.raises(InvalidIdentifierError):
        provider.get_user(email=email)
    assert _admin_calls(keycloak) == []


def test_get_user_rejects_a_non_uuid_id(keycloak, provider):
    with pytest.raises(InvalidIdentifierError):
        provider.get_user(user_id="not-a-uuid")
    assert _admin_calls(keycloak) == []


def test_get_user_requires_an_identifier(provider):
    with pytest.raises(InvalidIdentifierError):
        provider.get_user()


def test_get_username_returns_the_email(keycloak, provider):
    alice = keycloak.add_user("alice@example.com")
    assert provider.get_username(UUID(alice)) == "alice@example.com"


def test_set_enabled_updates_the_user(keycloak, provider):
    alice = keycloak.add_user("alice@example.com")
    provider.set_enabled("alice@example.com", False)
    assert keycloak.users[alice]["enabled"] is False
    provider.set_enabled("alice@example.com", True)
    assert keycloak.users[alice]["enabled"] is True


def test_set_enabled_for_an_unknown_user_is_not_found(provider):
    with pytest.raises(UserNotFoundError):
        provider.set_enabled("ghost@example.com", False)


def test_delete_user_removes_the_user(keycloak, provider):
    keycloak.add_user("alice@example.com")
    provider.delete_user("alice@example.com")
    assert keycloak.users == {}


def test_delete_unknown_user_is_not_found(provider):
    with pytest.raises(UserNotFoundError):
        provider.delete_user("ghost@example.com")


def test_a_user_deleted_between_lookup_and_delete_is_not_found(keycloak):
    """Two admins deleting at once: the loser's DELETE 404s, which the router treats as already gone."""
    alice = keycloak.add_user("alice@example.com")
    provider = _provider_where(keycloak, "DELETE", f"/users/{alice}", httpx.Response(404))
    with pytest.raises(UserNotFoundError, match="is not registered"):
        provider.delete_user("alice@example.com")


def test_get_user_by_id_of_a_service_account_is_not_found(keycloak, provider):
    """Service accounts carry no email and are not FLIP users, even when asked for by id."""
    keycloak.add_service_account()
    [service_account_id] = keycloak.users
    with pytest.raises(UserNotFoundError, match="is not registered"):
        provider.get_user(user_id=service_account_id)


# A 4xx other than not-found (e.g. 403 when the admin client lost a realm-management role) must
# surface as a provider error naming the operation, never as an empty directory or a silent no-op.
@pytest.mark.parametrize(
    ("method", "path_suffix", "operation", "message"),
    [
        ("GET", "/users", lambda p, _: p.list_users(), "Failed to list users"),
        ("GET", "/users", lambda p, _: p.get_user(email="alice@example.com"), "Failed to list users"),
        ("GET", "/users/{alice}", lambda p, alice: p.get_user(user_id=alice), "Failed to get user"),
        ("GET", "/users", lambda p, _: p.set_enabled("alice@example.com", False), "Failed to look up user"),
        ("PUT", "/users/{alice}", lambda p, _: p.set_enabled("alice@example.com", False), "Failed to update user"),
        ("DELETE", "/users/{alice}", lambda p, _: p.delete_user("alice@example.com"), "Failed to delete user"),
        ("GET", "/credentials", lambda p, _: p.is_mfa_enabled("alice@example.com"), "Failed to fetch MFA state"),
        ("DELETE", "/credentials/{otp}", lambda p, _: p.reset_mfa("alice@example.com"), "Failed to reset user MFA"),
        ("POST", "/logout", lambda p, _: p.reset_mfa("alice@example.com"), "Failed to reset user MFA"),
        ("PUT", "/reset-password", lambda p, _: p.set_password("alice@example.com", "pw"), "Failed to set password"),
    ],
)
def test_a_refused_admin_call_is_a_provider_error_naming_the_operation(
    keycloak, method, path_suffix, operation, message
):
    alice = keycloak.add_user("alice@example.com", otp=True)
    otp_id = next(c["id"] for c in keycloak.credentials[alice] if c["type"] == "otp")
    provider = _provider_where(
        keycloak, method, path_suffix.format(alice=alice, otp=otp_id), httpx.Response(403, json={"error": "Forbidden"})
    )
    with pytest.raises(IdentityProviderError, match=message) as exc_info:
        operation(provider, alice)
    assert not isinstance(exc_info.value, UserNotFoundError)


# --- create_user and the invitation ------------------------------------------------------


def test_create_user_registers_the_email_and_sends_the_set_password_email(keycloak, provider):
    user_id = provider.create_user("new@example.com")
    created = keycloak.users[str(user_id)]
    assert created["username"] == "new@example.com"
    assert created["email"] == "new@example.com"
    assert created["enabled"] is True
    assert created["emailVerified"] is True
    assert created["requiredActions"] == ["UPDATE_PASSWORD"]
    [(emailed_id, actions, params)] = keycloak.action_emails
    assert emailed_id == str(user_id)
    assert actions == ["UPDATE_PASSWORD"]
    assert params["client_id"] == "flip-ui"
    assert keycloak.password_resets == []


def test_create_user_with_suppress_invite_sends_nothing(keycloak, provider):
    provider.create_user("new@example.com", suppress_invite=True)
    assert keycloak.action_emails == []
    assert keycloak.password_resets == []


def test_create_existing_user_raises_already_exists(keycloak, provider):
    keycloak.add_user("new@example.com")
    with pytest.raises(UserAlreadyExistsError, match="already exists"):
        provider.create_user("new@example.com")


def test_create_user_refused_by_keycloak_is_a_provider_error(keycloak):
    provider = _provider_where(keycloak, "POST", "/users", httpx.Response(403, json={"error": "Forbidden"}))
    with patch("flip_api.auth.identity.keycloak.logger") as logger:
        with pytest.raises(IdentityProviderError, match="Failed to create user"):
            provider.create_user("new@example.com")
    assert "HTTP 403" in str(logger.error.call_args)
    assert keycloak.action_emails == []


def test_create_user_without_a_location_header_is_a_provider_error(keycloak):
    """The id comes from the Location header; without it there is nothing to hand the registration step."""
    provider = _provider_where(keycloak, "POST", "/users", httpx.Response(201))
    with pytest.raises(IdentityProviderError, match="could not get user ID"):
        provider.create_user("new@example.com")
    assert keycloak.action_emails == []
    assert keycloak.password_resets == []


def test_create_user_without_smtp_in_dev_hands_out_the_shared_dev_password_as_temporary(keycloak, provider):
    """Dev has no mail server: the invite Cognito would have emailed becomes ADMIN_USER_PASSWORD, temporary."""
    keycloak.smtp_configured = False
    with patch("flip_api.auth.identity.keycloak.logger") as logger:
        user_id = provider.create_user("new@example.com")
    [(reset_id, body)] = keycloak.password_resets
    assert reset_id == str(user_id)
    assert body == {"type": "password", "value": "Shared-Dev-Pa55word!", "temporary": True}  # pragma: allowlist secret
    logged = " ".join(str(call) for call in logger.warning.call_args_list)
    assert "new@example.com" in logged
    # Never the password itself: the log names the env var that holds it.
    assert "Shared-Dev-Pa55word!" not in logged  # pragma: allowlist secret
    assert "ADMIN_USER_PASSWORD" in logged


def test_create_user_without_smtp_outside_dev_only_warns(keycloak):
    provider = KeycloakIdentityProvider(_settings(env="production"), transport=httpx.MockTransport(keycloak.handler))
    keycloak.smtp_configured = False
    with patch("flip_api.auth.identity.keycloak.logger") as logger:
        provider.create_user("new@example.com")
    assert keycloak.password_resets == []
    logger.warning.assert_called()


def test_create_user_without_smtp_and_without_a_dev_password_only_warns(keycloak):
    settings = _settings()
    settings.ADMIN_USER_PASSWORD = None
    provider = KeycloakIdentityProvider(settings, transport=httpx.MockTransport(keycloak.handler))
    keycloak.smtp_configured = False
    with patch("flip_api.auth.identity.keycloak.logger") as logger:
        provider.create_user("new@example.com")
    assert keycloak.password_resets == []
    logger.warning.assert_called()


# --- MFA -------------------------------------------------------------------------------


def test_mfa_state_reflects_an_otp_credential(keycloak, provider):
    keycloak.add_user("enrolled@example.com", otp=True)
    keycloak.add_user("plain@example.com")
    assert provider.is_mfa_enabled("enrolled@example.com") is True
    assert provider.is_mfa_enabled("plain@example.com") is False


def test_reset_mfa_deletes_otp_credentials_and_logs_the_user_out(keycloak, provider):
    alice = keycloak.add_user("alice@example.com", otp=True)
    provider.reset_mfa("alice@example.com")
    assert [c["type"] for c in keycloak.credentials[alice]] == ["password"]
    assert keycloak.logged_out == [alice]
    assert provider.is_mfa_enabled("alice@example.com") is False


# --- origins -----------------------------------------------------------------------------


def test_allowed_origins_come_from_the_ui_clients_redirect_uris(keycloak, provider):
    assert provider.allowed_origins() == ["http://localhost:44350", "http://localhost:44351", "https://localhost"]


def test_allowed_origins_with_no_such_client_is_a_provider_error(keycloak):
    settings = _settings()
    settings.KEYCLOAK_CLIENT_ID = "missing-client"
    provider = KeycloakIdentityProvider(settings, transport=httpx.MockTransport(keycloak.handler))
    with pytest.raises(IdentityProviderError):
        provider.allowed_origins()


# --- operator tooling ------------------------------------------------------------------


def test_set_password_sets_a_permanent_password(keycloak, provider):
    alice = keycloak.add_user("alice@example.com")
    provider.set_password("alice@example.com", "Sup3r-secret!")  # pragma: allowlist secret
    [(reset_id, body)] = keycloak.password_resets
    assert reset_id == alice
    assert body == {"type": "password", "value": "Sup3r-secret!", "temporary": False}


# --- script helpers ----------------------------------------------------------------------


def test_password_grant_returns_the_token_response(keycloak):
    transport = httpx.MockTransport(keycloak.handler)
    tokens = password_grant(
        "http://localhost:8180", REALM, "flip-ui", "alice@example.com", "correct", transport=transport
    )
    assert tokens["access_token"] == "user-token"
    assert tokens["refresh_token"] == "refresh-1"
    form = parse_qs(keycloak.requests[-1].content.decode())
    assert form["grant_type"] == ["password"]
    assert form["client_id"] == ["flip-ui"]
    assert form["scope"] == ["openid"]


def test_password_grant_forwards_a_totp_code(keycloak):
    tokens = password_grant(
        "http://localhost:8180",
        REALM,
        "flip-ui",
        "alice@example.com",
        "correct",
        totp="123456",
        transport=httpx.MockTransport(keycloak.handler),
    )
    assert tokens["totp_seen"] == "123456"


def test_password_grant_failure_carries_keycloaks_description(keycloak):
    transport = httpx.MockTransport(keycloak.handler)
    with pytest.raises(IdentityProviderError, match="Invalid user credentials"):
        password_grant("http://localhost:8180", REALM, "flip-ui", "alice@example.com", "wrong", transport=transport)


def test_password_grant_against_an_unreachable_keycloak_is_unavailable():
    def refuses(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(IdentityProviderUnavailable, match="not reachable"):
        password_grant(
            "http://localhost:8180", REALM, "flip-ui", "a@example.com", "pw", transport=httpx.MockTransport(refuses)
        )


@pytest.mark.parametrize(
    "response",
    [httpx.Response(502, text="<html>Bad Gateway</html>"), httpx.Response(502, json=["not", "an", "object"])],
    ids=["html-body", "non-object-json"],
)
def test_a_grant_failure_without_an_oauth_error_body_names_the_status(response):
    """A proxy in front of Keycloak answers HTML, not RFC 6749 JSON; the message must still say what happened."""
    with pytest.raises(IdentityProviderError, match="HTTP 502"):
        refresh_grant(
            "http://localhost:8180", REALM, "flip-ui", "refresh-1", transport=httpx.MockTransport(lambda _: response)
        )


def test_refresh_grant_returns_the_new_tokens(keycloak):
    transport = httpx.MockTransport(keycloak.handler)
    tokens = refresh_grant("http://localhost:8180", REALM, "flip-ui", "refresh-1", transport=transport)
    assert tokens["access_token"] == "user-token-2"
    with pytest.raises(IdentityProviderError, match="Session not active"):
        refresh_grant("http://localhost:8180", REALM, "flip-ui", "stale", transport=transport)
