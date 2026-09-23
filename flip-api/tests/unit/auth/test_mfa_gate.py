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

"""End-to-end tests of the MFA gate at the FastAPI router level.

The unit tests in :mod:`test_dependencies` exercise ``verify_token`` /
``verify_token_no_mfa`` as plain functions. These tests wire a route to
the dependency the same way every flip-api router does and assert the
gate behaves correctly under HTTP — so a future refactor that replaces
``Depends(verify_token)`` with ``Depends(verify_token_no_mfa)`` on a
sensitive route is caught here, not in production.
"""

import uuid
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from flip_api.auth.dependencies import verify_token, verify_token_no_mfa
from flip_api.auth.token_verifier import VerifiedIdentity


@pytest.fixture
def user_sub() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def gated_app() -> FastAPI:
    """Tiny FastAPI app with one MFA-gated route and one bypass route."""
    app = FastAPI()

    @app.get("/gated")
    def gated_route(_user_id: uuid.UUID = Depends(verify_token)) -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/bypass")
    def bypass_route(_user_id: uuid.UUID = Depends(verify_token_no_mfa)) -> dict[str, str]:
        return {"status": "ok"}

    return app


def _payload(user_sub: str) -> VerifiedIdentity:
    return VerifiedIdentity(sub=uuid.UUID(user_sub), username="user@example.com")


def test_mfa_gated_route_returns_403_when_caller_has_no_active_totp(gated_app: FastAPI, user_sub: str) -> None:
    """A valid JWT against an MFA-gated route must be rejected with 403
    when the caller has no active TOTP. This is the load-bearing
    invariant of the entire gate — every guarded flip-api endpoint
    relies on this exact response shape."""
    with (
        patch("flip_api.auth.dependencies.verify_access_token") as mock_decode,
        patch("flip_api.auth.dependencies.is_mfa_enabled") as mock_is_enabled,
        patch("flip_api.auth.dependencies.get_settings") as mock_get_settings,
    ):
        mock_decode.return_value = _payload(user_sub)
        mock_is_enabled.return_value = False
        mock_get_settings.return_value.ENFORCE_MFA = True

        client = TestClient(gated_app)
        response = client.get("/gated", headers={"Authorization": "Bearer dummy"})

        assert response.status_code == 403
        assert response.json() == {"detail": "MFA enrolment required"}


def test_mfa_gated_route_returns_200_when_caller_has_active_totp(gated_app: FastAPI, user_sub: str) -> None:
    """The complementary case: a valid JWT plus active TOTP passes through."""
    with (
        patch("flip_api.auth.dependencies.verify_access_token") as mock_decode,
        patch("flip_api.auth.dependencies.is_mfa_enabled") as mock_is_enabled,
        patch("flip_api.auth.dependencies.get_settings") as mock_get_settings,
    ):
        mock_decode.return_value = _payload(user_sub)
        mock_is_enabled.return_value = True
        mock_get_settings.return_value.ENFORCE_MFA = True

        client = TestClient(gated_app)
        response = client.get("/gated", headers={"Authorization": "Bearer dummy"})

        assert response.status_code == 200


def test_bypass_route_admits_caller_without_totp(gated_app: FastAPI, user_sub: str) -> None:
    """``verify_token_no_mfa`` must let an unenrolled caller in — that's
    the whole point of the bootstrap endpoint. Otherwise a freshly-reset
    user could never reach /users/me/mfa/status to discover they need to
    re-enrol."""
    with (
        patch("flip_api.auth.dependencies.verify_access_token") as mock_decode,
        patch("flip_api.auth.dependencies.is_mfa_enabled") as mock_is_enabled,
    ):
        mock_decode.return_value = _payload(user_sub)
        # Even with MFA explicitly disabled on the user, the bypass route
        # should not consult is_mfa_enabled at all.
        mock_is_enabled.return_value = False

        client = TestClient(gated_app)
        response = client.get("/bypass", headers={"Authorization": "Bearer dummy"})

        assert response.status_code == 200
        mock_is_enabled.assert_not_called()


def test_mfa_gate_is_skipped_when_enforce_mfa_is_false(gated_app: FastAPI, user_sub: str) -> None:
    """Dev-only: with ``ENFORCE_MFA=false`` the gate is bypassed even on
    routes wired to ``verify_token``. Stag/prod must never see this."""
    with (
        patch("flip_api.auth.dependencies.verify_access_token") as mock_decode,
        patch("flip_api.auth.dependencies.is_mfa_enabled") as mock_is_enabled,
        patch("flip_api.auth.dependencies.get_settings") as mock_get_settings,
    ):
        mock_decode.return_value = _payload(user_sub)
        mock_is_enabled.return_value = False
        mock_get_settings.return_value.ENFORCE_MFA = False

        client = TestClient(gated_app)
        response = client.get("/gated", headers={"Authorization": "Bearer dummy"})

        assert response.status_code == 200
        mock_is_enabled.assert_not_called()


def _route_uses_dependency(route: APIRoute, target: Any) -> bool:
    """Walk a route's dependency tree and return True if ``target`` is
    invoked directly or transitively."""
    stack = list(route.dependant.dependencies)
    while stack:
        dep = stack.pop()
        if dep.call is target:
            return True
        stack.extend(dep.dependencies)
    return False


def test_only_mfa_status_route_uses_verify_token_no_mfa() -> None:
    """The MFA bypass dependency is reserved for the bootstrap endpoint.
    Anything else routing through ``verify_token_no_mfa`` is, by
    definition, an MFA bypass — failing this test is a security event,
    not a refactor blocker.

    Importing main.py registers every router, so the assertion runs
    against the real production wiring."""
    from flip_api.main import app

    bypass_routes = {
        f"{','.join(sorted(route.methods or []))} {route.path}"
        for route in app.routes
        if isinstance(route, APIRoute) and _route_uses_dependency(route, verify_token_no_mfa)
    }

    assert bypass_routes == {"GET /api/users/me/mfa/status"}, (
        "verify_token_no_mfa must only be used by the MFA bootstrap route. "
        f"Unexpected bypass routes detected: {bypass_routes - {'GET /api/users/me/mfa/status'}}"
    )
