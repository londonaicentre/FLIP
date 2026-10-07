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

"""The provider-neutral behaviour every IdentityProvider inherits (FLIP#919).

Exercised through a minimal in-memory provider so the tests pin the template
methods themselves — the MFA cache, the enabled-user filter, the username
lookup — independently of any backend.
"""

from unittest.mock import patch
from uuid import UUID, uuid4

import pytest

from flip_api.auth.identity.base import IdentityProvider
from flip_api.auth.identity.errors import UserNotFoundError
from flip_api.domain.schemas.users import CognitoUser


class InMemoryProvider(IdentityProvider):
    """The smallest concrete provider: a dict of users and a set of MFA-enrolled usernames."""

    backend = "memory"

    def __init__(self, users: list[CognitoUser], mfa_enrolled: set[str] | None = None) -> None:
        self.users = {user.id: user for user in users}
        self.mfa_enrolled = mfa_enrolled or set()
        self.mfa_fetches = 0
        self.mfa_resets: list[str] = []

    def list_users(self) -> list[CognitoUser]:
        return list(self.users.values())

    def get_user(self, *, user_id: UUID | str | None = None, email: str | None = None) -> CognitoUser:
        for user in self.users.values():
            if (user_id is not None and user.id == UUID(str(user_id))) or (email is not None and user.email == email):
                return user
        raise UserNotFoundError(f"User with email: {email} or ID: {user_id} is not registered.")

    def set_enabled(self, username: str, enabled: bool) -> None:
        raise NotImplementedError

    def create_user(self, email: str, *, suppress_invite: bool = False) -> UUID:
        raise NotImplementedError

    def delete_user(self, username: str) -> None:
        raise NotImplementedError

    def _fetch_mfa_enabled(self, username: str) -> bool:
        self.mfa_fetches += 1
        return username in self.mfa_enrolled

    def _reset_mfa(self, username: str) -> None:
        self.mfa_resets.append(username)
        self.mfa_enrolled.discard(username)

    def allowed_origins(self) -> list[str]:
        return []

    def set_password(self, email: str, password: str) -> None:
        raise NotImplementedError

    def describe_target(self) -> str:
        return "in-memory"


def _user(email: str, *, disabled: bool = False) -> CognitoUser:
    return CognitoUser(id=uuid4(), email=email, is_disabled=disabled)  # type: ignore[call-arg]


@pytest.fixture
def alice() -> CognitoUser:
    return _user("alice@example.com")


@pytest.fixture
def provider(alice) -> InMemoryProvider:
    return InMemoryProvider([alice, _user("bob@example.com", disabled=True)], mfa_enrolled={"alice@example.com"})


# --- get_username ------------------------------------------------------------------------


def test_get_username_returns_the_email_of_the_user_with_that_id(provider, alice):
    assert provider.get_username(alice.id) == "alice@example.com"
    assert provider.get_username(str(alice.id)) == "alice@example.com"


def test_get_username_surfaces_the_not_found_error(provider):
    with pytest.raises(UserNotFoundError):
        provider.get_username(uuid4())


# --- is_mfa_enabled cache ------------------------------------------------------------------


def test_mfa_state_positive_result_is_cached_within_the_ttl(provider):
    """verify_token asks on every request; an enrolled user must not cost a provider call each time."""
    assert provider.is_mfa_enabled("alice@example.com") is True
    assert provider.is_mfa_enabled("alice@example.com") is True
    assert provider.mfa_fetches == 1


def test_mfa_state_negative_result_is_never_cached(provider):
    """A user mid-enrolment must see their new state immediately, so negatives are re-fetched every time."""
    assert provider.is_mfa_enabled("bob@example.com") is False
    assert provider.is_mfa_enabled("bob@example.com") is False
    assert provider.mfa_fetches == 2


def test_mfa_state_cache_expires_after_the_ttl(provider):
    with patch("flip_api.auth.identity.base.time.monotonic", side_effect=[0.0, 0.0, 61.0, 61.0]):
        provider.is_mfa_enabled("alice@example.com")
        provider.is_mfa_enabled("alice@example.com")
    assert provider.mfa_fetches == 2


def test_reset_mfa_invalidates_the_cached_state(provider):
    """An admin reset must take effect on the very next request, not after the TTL."""
    assert provider.is_mfa_enabled("alice@example.com") is True
    provider.reset_mfa("alice@example.com")
    assert provider.mfa_resets == ["alice@example.com"]
    assert provider.is_mfa_enabled("alice@example.com") is False
    assert provider.mfa_fetches == 2


def test_mfa_cache_is_per_provider_instance(alice):
    """Two providers never share state, so a test double warmed in one cannot leak into another."""
    first = InMemoryProvider([alice], mfa_enrolled={"alice@example.com"})
    second = InMemoryProvider([alice], mfa_enrolled={"alice@example.com"})
    first.is_mfa_enabled("alice@example.com")
    second.is_mfa_enabled("alice@example.com")
    assert (first.mfa_fetches, second.mfa_fetches) == (1, 1)


# --- filter_enabled_users ----------------------------------------------------------------


def test_filter_enabled_users_keeps_only_known_enabled_ids(provider, alice):
    disabled_bob = next(u for u in provider.users.values() if u.email == "bob@example.com")
    unknown = uuid4()
    with patch("flip_api.auth.identity.base.logger") as logger:
        assert provider.filter_enabled_users([alice.id, disabled_bob.id, unknown]) == [alice.id]
    assert logger.warning.call_count == 2


def test_filter_enabled_users_short_circuits_on_an_empty_list(provider):
    with patch.object(provider, "list_users") as list_users:
        assert provider.filter_enabled_users([]) == []
    list_users.assert_not_called()
