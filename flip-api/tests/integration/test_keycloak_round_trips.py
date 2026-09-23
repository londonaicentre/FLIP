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

"""Real-Keycloak round-trips for the Keycloak identity provider + user_services (FLIP#919).

The twin of ``test_cognito_round_trips.py``: the pinned Keycloak image runs
under Testcontainers with the committed dev realm
(``deploy/keycloak/flip-realm.json``) imported at boot, so the production
code path runs end-to-end — ``register_user`` really creates a Keycloak
user, ``delete_user`` really removes one, and a token minted by Keycloak's
password grant really passes ``verify_token``. The same import proves the
realm file's ``${...}`` placeholders resolve (the seeded admin can sign in
with ``ADMIN_USER_PASSWORD``).
"""

import socket
import time
from collections.abc import Generator
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlmodel import select
from testcontainers.core.container import DockerContainer

from flip_api.auth import token_verifier
from flip_api.auth.dependencies import verify_token
from flip_api.auth.identity import build_identity_provider
from flip_api.auth.identity.factory import _cached_provider
from flip_api.auth.identity.keycloak import password_grant
from flip_api.config import get_settings
from flip_api.db.models.main_models import Trust
from flip_api.db.models.user_models import RoleRef, UserRole, UsersAudit
from flip_api.main import app
from flip_api.utils.constants import ADMIN_EMAIL_1
from tests.integration.conftest import admin_user, override_verify_token_as

KEYCLOAK_IMAGE = "quay.io/keycloak/keycloak:26.7.4"  # keep in step with deploy/compose.development.yml
REALM_FILE = Path(__file__).resolve().parents[3] / "deploy" / "keycloak" / "flip-realm.json"
SEEDED_PASSWORD = "Round-Trip-Pa55word!"  # pragma: allowlist secret
CHOSEN_PASSWORD = "Chosen-By-The-User-Pa55!"  # pragma: allowlist secret
ADMIN_CLIENT_SECRET = "round-trip-admin-secret"  # pragma: allowlist secret
SEEDED_ADMIN_SUB = UUID("0f1b6a2e-0001-4919-8000-000000000001")  # ADMIN_EMAIL_1's fixed id in the realm file


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def keycloak_url() -> Generator[str, None, None]:
    """Boot the dev realm once for the module and return its public URL.

    The host port is chosen up front because ``KC_HOSTNAME`` (which pins the
    ``iss`` claim) has to be known before the container starts; the same URL
    then serves as both ``KEYCLOAK_URL`` and ``KEYCLOAK_PUBLIC_URL``, exactly
    the dev-compose arrangement collapsed onto one host.
    """
    port = _free_port()
    url = f"http://localhost:{port}"
    container = (
        DockerContainer(KEYCLOAK_IMAGE)
        .with_command("start-dev --import-realm")
        .with_env("KC_BOOTSTRAP_ADMIN_USERNAME", "admin")
        .with_env("KC_BOOTSTRAP_ADMIN_PASSWORD", "admin")
        .with_env("KC_HTTP_ENABLED", "true")
        .with_env("KC_HOSTNAME", url)
        .with_env("ADMIN_USER_PASSWORD", SEEDED_PASSWORD)
        .with_env("KEYCLOAK_ADMIN_CLIENT_SECRET", ADMIN_CLIENT_SECRET)
        .with_env("UI_PORT", "44350")
        .with_volume_mapping(str(REALM_FILE), "/opt/keycloak/data/import/flip-realm.json", "ro")
        .with_bind_ports(8080, port)
    )
    with container:
        deadline = time.monotonic() + 180
        while True:
            try:
                if httpx.get(f"{url}/realms/flip/.well-known/openid-configuration", timeout=5).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                raise RuntimeError(f"Keycloak did not serve the flip realm within 180 s:\n{container.get_logs()}")
            time.sleep(2)
        yield url


@pytest.fixture
def keycloak_backend(keycloak_url: str, monkeypatch) -> Generator[str, None, None]:
    """Point ``Settings`` at the container and drop the cached provider/JWKS client."""
    settings = get_settings()
    monkeypatch.setattr(settings, "AUTH_BACKEND", "keycloak")
    monkeypatch.setattr(settings, "KEYCLOAK_URL", keycloak_url)
    monkeypatch.setattr(settings, "KEYCLOAK_PUBLIC_URL", keycloak_url)
    monkeypatch.setattr(settings, "KEYCLOAK_REALM", "flip")
    monkeypatch.setattr(settings, "KEYCLOAK_CLIENT_ID", "flip-ui")
    monkeypatch.setattr(settings, "KEYCLOAK_AUDIENCE", "flip-api")
    monkeypatch.setattr(settings, "KEYCLOAK_ADMIN_CLIENT_ID", "flip-api-admin")
    monkeypatch.setattr(settings, "KEYCLOAK_ADMIN_CLIENT_SECRET", SecretStr(ADMIN_CLIENT_SECRET))
    _cached_provider.cache_clear()
    token_verifier._jwks_client.cache_clear()
    yield keycloak_url
    _cached_provider.cache_clear()
    token_verifier._jwks_client.cache_clear()


def _sign_in(keycloak_url: str, email: str, password: str) -> dict:
    return password_grant(keycloak_url, "flip", "flip-ui", email, password)


# --- the realm import itself ------------------------------------------------------------


def test_realm_import_resolved_the_placeholders(keycloak_backend):
    """The seeded admin signs in with ADMIN_USER_PASSWORD and the admin client with its secret."""
    tokens = _sign_in(keycloak_backend, ADMIN_EMAIL_1, SEEDED_PASSWORD)
    assert tokens["access_token"]
    provider = build_identity_provider()
    assert provider.get_user(email=ADMIN_EMAIL_1).id == SEEDED_ADMIN_SUB


def test_allowed_origins_come_from_the_realm(keycloak_backend):
    origins = build_identity_provider().allowed_origins()
    assert "http://localhost:44350" in origins
    assert "http://localhost:44359" in origins


# --- a real token through verify_token ----------------------------------------------------


def test_password_grant_token_authenticates_against_the_hub(client: TestClient, session, keycloak_backend):
    """No dependency override here: the bearer token goes through the real verifier and MFA gate."""
    session.add(UserRole(user_id=SEEDED_ADMIN_SUB, role_id=RoleRef.ADMIN.value))
    session.commit()
    tokens = _sign_in(keycloak_backend, ADMIN_EMAIL_1, SEEDED_PASSWORD)

    settings = get_settings()
    original = settings.ENFORCE_MFA
    settings.ENFORCE_MFA = False
    try:
        response = client.get("/api/users/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    finally:
        settings.ENFORCE_MFA = original
    assert response.status_code == 200, response.text
    assert response.json()["email"] == ADMIN_EMAIL_1
    assert response.json()["id"] == str(SEEDED_ADMIN_SUB)


def test_mfa_gate_refuses_a_user_without_an_otp_credential(client: TestClient, session, keycloak_backend):
    session.add(UserRole(user_id=SEEDED_ADMIN_SUB, role_id=RoleRef.ADMIN.value))
    session.commit()
    tokens = _sign_in(keycloak_backend, ADMIN_EMAIL_1, SEEDED_PASSWORD)

    settings = get_settings()
    original = settings.ENFORCE_MFA
    settings.ENFORCE_MFA = True
    try:
        response = client.get("/api/users/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    finally:
        settings.ENFORCE_MFA = original
    assert response.status_code == 403, response.text
    assert response.json() == {"detail": "MFA enrolment required"}


def test_an_id_token_is_not_accepted_as_authorisation(client: TestClient, session, keycloak_backend):
    tokens = _sign_in(keycloak_backend, ADMIN_EMAIL_1, SEEDED_PASSWORD)
    response = client.get("/api/users/me", headers={"Authorization": f"Bearer {tokens['id_token']}"})
    assert response.status_code == 401, response.text


# --- user_services against the real admin API -------------------------------------------------


def test_register_user_creates_a_keycloak_user_and_writes_audit(client: TestClient, session, keycloak_backend):
    admin_id = admin_user(session)
    override_verify_token_as(admin_id)
    email = f"new-{uuid4().hex[:8]}@example.com"

    response = client.post(
        "/api/users",
        json={
            "email": email,
            "name": "New User",
            "organisation": "Test Hospital",
            "roles": [str(RoleRef.RESEARCHER.value)],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    new_user_id = UUID(body.get("userId") or body["user_id"])

    created = build_identity_provider().get_user(email=email)
    assert created.id == new_user_id
    assert created.is_disabled is False

    audit = session.exec(
        select(UsersAudit).where(UsersAudit.user_id == new_user_id).where(UsersAudit.action == "Registered user")
    ).first()
    assert audit is not None
    assert audit.modified_by_user_id == admin_id


def test_a_registered_user_signs_in_once_their_password_is_set(client: TestClient, session, keycloak_backend):
    """FLIP keeps a user's name in its own tables, so the realm must not demand one from Keycloak.

    Keycloak's default user profile requires firstName/lastName and, with VERIFY_PROFILE on, refuses the
    UI's password grant ("Account is not fully set up") for a user without them — which is every user
    FLIP registers. The realm file makes both optional.
    """
    admin_id = admin_user(session)
    override_verify_token_as(admin_id)
    email = f"signin-{uuid4().hex[:8]}@example.com"
    response = client.post(
        "/api/users",
        json={
            "email": email,
            "name": "New User",
            "organisation": "Test Hospital",
            "roles": [str(RoleRef.RESEARCHER.value)],
        },
    )
    assert response.status_code == 200, response.text

    # The account-console step: the user replaces the temporary password with their own, which also
    # clears the UPDATE_PASSWORD action registration left.
    build_identity_provider().set_password(email, CHOSEN_PASSWORD)
    tokens = _sign_in(keycloak_backend, email, CHOSEN_PASSWORD)
    assert tokens["access_token"]


def test_register_user_duplicate_email_returns_400(client: TestClient, session, keycloak_backend):
    admin_id = admin_user(session)
    override_verify_token_as(admin_id)

    response = client.post(
        "/api/users",
        json={
            "email": ADMIN_EMAIL_1,
            "name": "Dupe",
            "organisation": "Test Hospital",
            "roles": [str(RoleRef.RESEARCHER.value)],
        },
    )
    assert response.status_code == 400, response.text
    assert "already exists" in response.text.lower()


def test_delete_user_removes_the_keycloak_user_and_drops_role_grants(client: TestClient, session, keycloak_backend):
    admin_id = admin_user(session)
    override_verify_token_as(admin_id)
    provider = build_identity_provider()
    email = f"delete-{uuid4().hex[:8]}@example.com"
    sub = provider.create_user(email, suppress_invite=True)
    session.add(UserRole(user_id=sub, role_id=RoleRef.RESEARCHER.value))
    session.commit()

    response = client.delete(f"/api/users/{sub}")
    assert response.status_code == 200, response.text

    assert not any(user.id == sub for user in provider.list_users())
    assert session.exec(select(UserRole).where(UserRole.user_id == sub)).all() == []
    audit = session.exec(
        select(UsersAudit).where(UsersAudit.user_id == sub).where(UsersAudit.action == "Deleted user")
    ).first()
    assert audit is not None


def test_update_user_toggles_the_enabled_flag(client: TestClient, session, keycloak_backend):
    admin_id = admin_user(session)
    override_verify_token_as(admin_id)
    provider = build_identity_provider()
    email = f"toggle-{uuid4().hex[:8]}@example.com"
    sub = provider.create_user(email, suppress_invite=True)
    # update_user enqueues an UPDATE_USER_PROFILE TrustTask per registered trust.
    session.add(Trust(name="XNAT Sync Trust"))
    session.commit()

    response = client.put(f"/api/users/{sub}", json={"disabled": True})
    assert response.status_code == 200, response.text
    assert provider.get_user(user_id=sub).is_disabled is True


def test_set_roles_for_an_unknown_user_returns_404(client: TestClient, session, keycloak_backend):
    admin_id = admin_user(session)
    override_verify_token_as(admin_id)
    response = client.post(f"/api/users/{uuid4()}/roles", json={"roles": [str(RoleRef.RESEARCHER.value)]})
    assert response.status_code == 404, response.text


def test_reset_mfa_logs_the_user_out(client: TestClient, session, keycloak_backend):
    """The seeded admin has no OTP credential; the reset still revokes their sessions."""
    admin_id = admin_user(session)
    override_verify_token_as(admin_id)
    tokens = _sign_in(keycloak_backend, ADMIN_EMAIL_1, SEEDED_PASSWORD)

    response = client.post(f"/api/users/{SEEDED_ADMIN_SUB}/mfa/reset")
    assert response.status_code == 200, response.text

    # The refresh token minted before the reset no longer works.
    refresh = httpx.post(
        f"{keycloak_backend}/realms/flip/protocol/openid-connect/token",
        data={"grant_type": "refresh_token", "client_id": "flip-ui", "refresh_token": tokens["refresh_token"]},
    )
    assert refresh.status_code == 400
    app.dependency_overrides.pop(verify_token, None)
