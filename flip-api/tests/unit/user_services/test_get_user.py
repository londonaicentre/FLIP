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

from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException, status
from fastapi.testclient import TestClient

from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.domain.schemas.users import CognitoUser
from flip_api.main import app

client = TestClient(app)

MODULE = "flip_api.user_services.get_user"


# ---------------------
# Fixtures
# ---------------------


@pytest.fixture
def caller_id():
    """The authenticated caller's identity-provider ``sub``."""
    return uuid4()


@pytest.fixture(autouse=True)
def override_deps(caller_id):
    """Override auth and DB dependencies for all tests using TestClient."""
    app.dependency_overrides[verify_token] = lambda: caller_id
    app.dependency_overrides[get_session] = lambda: None
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def profiled_user():
    """An identity-provider user carrying the DB-backed profile fields that must not leak from /lookup."""
    return CognitoUser(
        id=uuid4(),
        email="test.user@example.com",
        name="Dr Test User",
        organisation="King's College London",
        is_disabled=False,
    )


# ---------------------
# GET /users/{user_id} — serialization
# ---------------------


def test_get_user_by_uuid_response_serialization(fake_idp):
    """Test that the endpoint correctly serializes a CognitoUser response when looked up by UUID."""
    user_uuid = uuid4()
    cognito_user = CognitoUser(id=user_uuid, email="test@example.com", is_disabled=True)
    fake_idp.get_user.return_value = cognito_user

    with patch(f"{MODULE}.has_permissions", return_value=True):
        response = client.get(f"/api/users/{user_uuid}")

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["id"] == str(user_uuid)
    assert data["isDisabled"] is True


# ---------------------
# GET /users/{user_id} — lookup behaviour for an authorised caller
# ---------------------


def test_get_user_by_uuid(fake_idp, user_id, user_data):
    """Test successfully retrieving a user by UUID."""
    fake_idp.get_user.return_value = user_data

    with patch(f"{MODULE}.has_permissions", return_value=True):
        response = client.get(f"/api/users/{user_id}")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["id"] == str(user_data.id)
    fake_idp.get_user.assert_called_once_with(user_id=UUID(user_id))


@pytest.mark.parametrize("bad_id", ["not-an-email-or-uuid", "someone@example.com"])
def test_non_uuid_user_id_is_rejected_by_parameter_validation(bad_id):
    """A non-UUID segment fails parameter validation with a message naming the problem.

    `user_id` is typed as a UUID, so FastAPI rejects anything else before the handler runs — the
    same 422 every sibling route under /users produces. The email form this endpoint used to
    accept falls into that bucket now that the member lookup lives at /users/lookup (FLIP#907).

    422 rather than a router-level 404 is deliberate: it tells the caller *why* the id was
    rejected, and it discloses nothing, since the shape of an id is independent of whether any
    account exists.
    """
    with patch(f"{MODULE}.has_permissions", return_value=True) as mock_has_permissions:
        response = client.get(f"/api/users/{bad_id}")

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert "valid UUID" in response.text
    mock_has_permissions.assert_not_called()


def test_user_not_found(fake_idp, user_id):
    """Test when user is not found in the identity provider.

    ``IdentityProvider.get_user`` raises the 404 itself and is annotated non-Optional, so this
    asserts the propagated error rather than a falsy-return branch the provider cannot produce.
    """
    fake_idp.get_user.side_effect = HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"User with email: None or ID: {user_id} is not registered.",
    )

    with patch(f"{MODULE}.has_permissions", return_value=True):
        response = client.get(f"/api/users/{user_id}")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert "is not registered" in response.json()["detail"]


def test_internal_server_error(fake_idp, user_id):
    """Test handling of internal server errors."""
    fake_idp.get_user.side_effect = Exception("Test exception")

    with (
        patch(f"{MODULE}.has_permissions", return_value=True),
        patch(f"{MODULE}.logger") as mock_logger,
    ):
        response = client.get(f"/api/users/{user_id}")

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert response.json()["detail"] == "Internal server error"
    assert "Test exception" not in response.json()["detail"]
    mock_logger.exception.assert_called_once()


# ---------------------
# Route ordering
# ---------------------


@pytest.mark.parametrize("static_path", ["/api/users/me", "/api/users/lookup", "/api/users/access"])
def test_static_user_routes_are_matched_before_the_catch_all(static_path):
    """Every static segment under /api/users must be registered before GET /api/users/{user_id}.

    `/{user_id}` matches any single segment — "me", "lookup" and "access" included. Starlette takes
    the first match, so a static route registered after it is silently unreachable and answers as
    the catch-all instead.

    `/me` and `/lookup` are pinned by their declaration order within get_user.py, but
    `/api/users/access` lives in access_request.py and depends on the ordering of the ROUTERS tuple
    in main.py, which nothing else enforces. This covers both.
    """

    def first_get_index(path):
        return next(
            i
            for i, route in enumerate(app.routes)
            if getattr(route, "path", None) == path and "GET" in (getattr(route, "methods", None) or set())
        )

    assert first_get_index(static_path) < first_get_index("/api/users/{user_id}")


def test_static_user_routes_resolve_to_their_own_handlers(fake_idp):
    """Belt and braces: each static route actually answers, rather than falling into the catch-all.

    The index assertion above would still pass if a static route were somehow shadowed by a route
    registered even earlier, so confirm the responses are not the catch-all's.
    """
    with (
        patch(f"{MODULE}.has_permissions", return_value=False),
        patch(f"{MODULE}.has_any_permission", return_value=False),
    ):
        # The catch-all denies with 403; /lookup denies with its own 403 detail, and /me needs no
        # permission at all — so neither can be answering as get_user.
        assert client.get("/api/users/lookup", params={"email": "a@b.com"}).json()["detail"] == (
            "User is not permitted to look up project members."
        )
        assert client.get(f"/api/users/{uuid4()}").status_code == status.HTTP_403_FORBIDDEN


# ---------------------
# GET /users/{user_id} — authorization (FLIP#907)
# ---------------------


class TestGetUserAuthorization:
    """The route is administrative: CAN_MANAGE_USERS only, with no self escape hatch."""

    def test_caller_without_permission_is_forbidden(self, fake_idp, user_id):
        """A caller lacking CAN_MANAGE_USERS cannot read another user's profile."""
        with patch(f"{MODULE}.has_permissions", return_value=False):
            response = client.get(f"/api/users/{user_id}")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        # The permission check must precede the lookup, or the 403-vs-404 split leaks whether the
        # account exists.
        fake_idp.get_user.assert_not_called()

    def test_denial_is_identical_for_a_user_that_does_not_exist(self, fake_idp, user_id):
        """An unauthorised caller cannot tell a real account from an absent one."""
        fake_idp.get_user.side_effect = HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="is not registered."
        )

        with patch(f"{MODULE}.has_permissions", return_value=False):
            response = client.get(f"/api/users/{user_id}")

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_self_lookup_is_not_a_backdoor(self, fake_idp, caller_id):
        """Reading your own record through this route is denied too — /users/me is the self path."""
        with patch(f"{MODULE}.has_permissions", return_value=False):
            response = client.get(f"/api/users/{caller_id}")

        assert response.status_code == status.HTTP_403_FORBIDDEN


# ---------------------
# GET /users/me (FLIP#907)
# ---------------------


class TestGetCurrentUser:
    """Every authenticated user can read their own profile, and only their own."""

    def test_returns_own_profile_without_any_permission(self, fake_idp, caller_id, profiled_user):
        """The token is the authorization — no permission is required or consulted."""
        fake_idp.get_user.return_value = profiled_user

        with (
            patch(f"{MODULE}.has_permissions", return_value=False) as mock_has_permissions,
            patch(f"{MODULE}.has_any_permission", return_value=False) as mock_has_any,
        ):
            response = client.get("/api/users/me")

        assert response.status_code == status.HTTP_200_OK
        mock_has_permissions.assert_not_called()
        mock_has_any.assert_not_called()
        # Resolved from the token's sub, never from a caller-supplied email.
        fake_idp.get_user.assert_called_once_with(user_id=caller_id)

    def test_returns_full_profile_fields(self, fake_idp, profiled_user):
        """The self route keeps the DB-backed profile fields the header display name needs."""
        fake_idp.get_user.return_value = profiled_user

        with patch(f"{MODULE}.apply_user_profile", return_value=profiled_user) as mock_apply:
            response = client.get("/api/users/me")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["name"] == "Dr Test User"
        assert data["organisation"] == "King's College London"
        assert data["isDisabled"] is False
        mock_apply.assert_called_once()

    def test_404_when_token_subject_has_no_identity_record(self, fake_idp):
        """A valid token whose sub no longer resolves is a genuine 404."""
        fake_idp.get_user.side_effect = HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="is not registered."
        )

        response = client.get("/api/users/me")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_route_is_not_shadowed_by_the_catch_all(self, fake_idp, profiled_user):
        """Regression: /users/me must not be matched as /users/{user_id}.

        `/{user_id}` is a bare `str` param that would happily swallow "me" and reject it as an
        invalid id format. This fails if the decorators in get_user.py are ever reordered.
        """
        fake_idp.get_user.return_value = profiled_user

        response = client.get("/api/users/me")

        assert response.status_code == status.HTTP_200_OK
        assert "Invalid user ID format" not in response.text


# ---------------------
# GET /users/lookup (FLIP#907)
# ---------------------


class TestLookupProjectMember:
    """The narrow member lookup: enough to add a project member, and nothing more."""

    def test_caller_without_permission_is_forbidden(self, fake_idp):
        """A Viewer cannot use the lookup, and learns nothing about the address."""
        with patch(f"{MODULE}.has_any_permission", return_value=False):
            response = client.get("/api/users/lookup", params={"email": "someone@example.com"})

        assert response.status_code == status.HTTP_403_FORBIDDEN
        fake_idp.get_user.assert_not_called()

    def test_returns_only_the_narrow_record(self, fake_idp, profiled_user):
        """The whole point of FLIP#907: name and organisation must never appear here."""
        fake_idp.get_user.return_value = profiled_user

        with patch(f"{MODULE}.has_any_permission", return_value=True):
            response = client.get("/api/users/lookup", params={"email": profiled_user.email})

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert set(data) == {"id", "email", "isDisabled"}
        assert data["id"] == str(profiled_user.id)
        assert data["email"] == profiled_user.email
        # Resolved by the queried address, never by id.
        fake_idp.get_user.assert_called_once_with(email=profiled_user.email)

    def test_does_not_apply_the_db_profile(self, fake_idp, profiled_user):
        """apply_user_profile is the only source of name/organisation, so it must not be called."""
        fake_idp.get_user.return_value = profiled_user

        with (
            patch(f"{MODULE}.has_any_permission", return_value=True),
            patch(f"{MODULE}.apply_user_profile") as mock_apply,
        ):
            response = client.get("/api/users/lookup", params={"email": profiled_user.email})

        assert response.status_code == status.HTTP_200_OK
        mock_apply.assert_not_called()

    def test_reports_disabled_accounts(self, fake_idp, profiled_user):
        """The UI rejects disabled accounts before submitting, so the flag has to come through."""
        disabled = profiled_user.model_copy(update={"is_disabled": True})
        fake_idp.get_user.return_value = disabled

        with patch(f"{MODULE}.has_any_permission", return_value=True):
            response = client.get("/api/users/lookup", params={"email": disabled.email})

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["isDisabled"] is True

    def test_unknown_email_returns_a_generic_not_found(self, fake_idp):
        """The 404 detail must not echo the queried address back to the caller."""
        email = "nobody@example.com"
        fake_idp.get_user.side_effect = HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with email: {email} or ID: None is not registered.",
        )

        with patch(f"{MODULE}.has_any_permission", return_value=True):
            response = client.get("/api/users/lookup", params={"email": email})

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.json()["detail"] == "User not found."
        assert email not in response.text

    def test_a_provider_failure_is_not_reported_as_a_missing_user(self, fake_idp):
        """Only the 404 gets rewritten — anything else propagates unchanged.

        The identity provider raises a sanitised 500 when its user listing fails. Folding that
        into the "User not found." branch would report an outage as a missing account, hiding
        the failure from the caller and from anyone reading the logs.
        """
        fake_idp.get_user.side_effect = HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error while listing users",
        )

        with patch(f"{MODULE}.has_any_permission", return_value=True):
            response = client.get("/api/users/lookup", params={"email": "someone@example.com"})

        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert response.json()["detail"] == "Internal server error while listing users"

    def test_rejects_a_malformed_email(self, fake_idp):
        """A non-email query value is a validation error, not an identity-provider call."""
        with patch(f"{MODULE}.has_any_permission", return_value=True):
            response = client.get("/api/users/lookup", params={"email": "not-an-email"})

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        fake_idp.get_user.assert_not_called()

    def test_requires_the_email_parameter(self):
        """Regression: /users/lookup must not be matched as /users/{user_id}.

        A missing `email` is a 422 from query validation. If the catch-all won the match instead,
        "lookup" would be rejected as an invalid id format (400) or denied outright (403).
        """
        with patch(f"{MODULE}.has_any_permission", return_value=True):
            response = client.get("/api/users/lookup")

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert "Invalid user ID format" not in response.text
