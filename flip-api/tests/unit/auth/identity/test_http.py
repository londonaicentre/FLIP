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

"""HttpIdentityProvider: the one place provider errors become HTTP status codes (FLIP#919).

Routers keep the ``except HTTPException`` branches they always had (404 means
"no such user", 503 means "could not ask the provider", ...), so this table is
a contract every provider is held to.
"""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException, status

from flip_api.auth.identity.base import IdentityProvider
from flip_api.auth.identity.errors import (
    IdentityProviderError,
    IdentityProviderUnavailable,
    InvalidIdentifierError,
    UserAlreadyExistsError,
    UserNotFoundError,
)
from flip_api.auth.identity.http import HttpIdentityProvider


@pytest.fixture
def inner() -> MagicMock:
    return MagicMock(spec=IdentityProvider)


@pytest.fixture
def provider(inner) -> HttpIdentityProvider:
    return HttpIdentityProvider(inner)


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (UserNotFoundError("User with ID x is not registered."), status.HTTP_404_NOT_FOUND),
        (UserAlreadyExistsError("User with email a@b.c already exists"), status.HTTP_400_BAD_REQUEST),
        (InvalidIdentifierError("Invalid user ID format"), status.HTTP_400_BAD_REQUEST),
        (IdentityProviderUnavailable("Keycloak is not reachable"), status.HTTP_503_SERVICE_UNAVAILABLE),
        (IdentityProviderError("Failed to list users"), status.HTTP_500_INTERNAL_SERVER_ERROR),
    ],
)
def test_each_provider_error_maps_to_its_status_and_keeps_its_message(provider, inner, error, expected_status):
    inner.get_username.side_effect = error
    with pytest.raises(HTTPException) as exc_info:
        provider.get_username(uuid4())
    assert exc_info.value.status_code == expected_status
    assert exc_info.value.detail == str(error)
    assert exc_info.value.__cause__ is error


def test_results_pass_through_unchanged(provider, inner):
    inner.get_username.return_value = "user@example.com"
    inner.allowed_origins.return_value = ["https://app.example.com"]
    assert provider.get_username(uuid4()) == "user@example.com"
    assert provider.allowed_origins() == ["https://app.example.com"]


def test_every_directory_method_is_delegated_with_its_arguments(provider, inner):
    user_id = uuid4()
    provider.list_users()
    provider.get_user(user_id=user_id)
    provider.get_user(email="a@b.c")
    provider.set_enabled("a@b.c", False)
    provider.create_user("a@b.c", suppress_invite=True)
    provider.delete_user("a@b.c")
    provider.is_mfa_enabled("a@b.c")
    provider.reset_mfa("a@b.c")
    provider.filter_enabled_users([user_id])
    provider.set_password("a@b.c", "pw")
    provider.describe_target()

    inner.list_users.assert_called_once_with()
    inner.get_user.assert_any_call(user_id=user_id, email=None)
    inner.get_user.assert_any_call(user_id=None, email="a@b.c")
    inner.set_enabled.assert_called_once_with("a@b.c", False)
    inner.create_user.assert_called_once_with("a@b.c", suppress_invite=True)
    inner.delete_user.assert_called_once_with("a@b.c")
    inner.is_mfa_enabled.assert_called_once_with("a@b.c")
    inner.reset_mfa.assert_called_once_with("a@b.c")
    inner.filter_enabled_users.assert_called_once_with([user_id])
    inner.set_password.assert_called_once_with("a@b.c", "pw")
    inner.describe_target.assert_called_once_with()


def test_an_http_exception_from_the_inner_provider_is_not_rewrapped(provider, inner):
    """A test double may speak HTTP directly (the router tests do); it must come out untouched."""
    original = HTTPException(status_code=status.HTTP_418_IM_A_TEAPOT, detail="brewing")
    inner.delete_user.side_effect = original
    with pytest.raises(HTTPException) as exc_info:
        provider.delete_user("a@b.c")
    assert exc_info.value is original


def test_the_wrapper_reports_the_inner_backend(provider, inner):
    inner.backend = "cognito"
    assert provider.backend == "cognito"
