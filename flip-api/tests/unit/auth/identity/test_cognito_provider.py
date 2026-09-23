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

"""Unit tests for ``CognitoIdentityProvider`` (``flip_api.auth.identity.cognito``).

Ported from the ``flip_api.utils.cognito_helpers`` suite (FLIP#919). The provider holds
its boto3 client per instance, so every test gets a fresh provider from the ``provider``
fixture and no cross-test cache reset is needed. Cognito-specific methods are driven
through the mocked boto3 client / ``list_users`` paginator; the concrete base-class
methods (``filter_enabled_users``, ``is_mfa_enabled`` cache, ``get_username``) are
exercised through the provider so the base + Cognito wiring is what gets pinned.
"""

from unittest.mock import MagicMock, call, patch
from uuid import UUID, uuid4

import factory
import pytest
from botocore.exceptions import ClientError

from flip_api.auth.identity.cognito import CognitoIdentityProvider
from flip_api.auth.identity.errors import (
    IdentityProviderError,
    IdentityProviderUnavailable,
    InvalidIdentifierError,
    UserAlreadyExistsError,
    UserNotFoundError,
)
from flip_api.domain.schemas.users import CognitoUser

user1, user2, user3, user4, user5, user6 = [uuid4() for _ in range(6)]
USER_POOL_ID = "test-user-pool-id"
APP_CLIENT_ID = "client-id"
REGION = "eu-west-2"


class CognitoUserFactory(factory.Factory):
    """Factory for creating CognitoUser objects."""

    class Meta:
        model = CognitoUser

    id = factory.Faker("uuid4")
    email = factory.Faker("email")
    is_disabled = factory.Faker("boolean")


def _settings(region: str = REGION, pool_id: str = USER_POOL_ID, client_id: str = APP_CLIENT_ID) -> MagicMock:
    settings = MagicMock()
    settings.AWS_REGION = region
    settings.AWS_COGNITO_USER_POOL_ID = pool_id
    settings.AWS_COGNITO_APP_CLIENT_ID = client_id
    return settings


def _client_error(code: str, message: str, operation: str) -> ClientError:
    return ClientError(error_response={"Error": {"Code": code, "Message": message}}, operation_name=operation)


def _cognito_user(sub: UUID, username: str, *, email: str | None = None, enabled: bool | None = True) -> dict:
    """Build one entry of a Cognito ``ListUsers`` page."""
    attributes = [{"Name": "sub", "Value": str(sub)}]
    if email is not None:
        attributes.append({"Name": "email", "Value": email})
    user: dict = {"Username": username, "Attributes": attributes}
    if enabled is not None:
        user["Enabled"] = enabled
    return user


def _wire_pages(cognito_client: MagicMock, *pages: dict) -> MagicMock:
    """Make the ``list_users`` paginator yield ``pages`` and return its ``paginate`` mock."""
    paginate = cognito_client.get_paginator.return_value.paginate
    paginate.return_value = list(pages)
    return paginate


def _all_logged_text(mock_logger: MagicMock) -> str:
    """Every positional arg of every call on the mocked logger, joined — for PII-leak asserts."""
    return " ".join(str(arg) for method_call in mock_logger.mock_calls for arg in method_call.args)


@pytest.fixture
def boto3_client_cls():
    """Patch ``boto3.client`` where the provider imports it; yields the class mock."""
    with patch("flip_api.auth.identity.cognito.boto3.client") as client_cls:
        yield client_cls


@pytest.fixture
def cognito_client(boto3_client_cls):
    """The cognito-idp client instance the provider will build lazily."""
    return boto3_client_cls.return_value


@pytest.fixture
def provider(cognito_client):
    return CognitoIdentityProvider(_settings())


@pytest.fixture
def cognito_logger():
    with patch("flip_api.auth.identity.cognito.logger") as mock_logger:
        yield mock_logger


@pytest.fixture
def base_logger():
    with patch("flip_api.auth.identity.base.logger") as mock_logger:
        yield mock_logger


@pytest.fixture
def sample_page():
    """One ``ListUsers`` page: user1 enabled, user2 disabled."""
    return {
        "Users": [
            {
                "Username": "user1@example.com",
                "Attributes": [
                    {"Name": "sub", "Value": str(user1)},
                    {"Name": "email", "Value": "user1@example.com"},
                    {"Name": "email_verified", "Value": "true"},
                ],
                "UserCreateDate": "2023-01-01T00:00:00Z",
                "UserLastModifiedDate": "2023-01-01T00:00:00Z",
                "Enabled": True,
                "UserStatus": "CONFIRMED",
            },
            {
                "Username": "user2@example.com",
                "Attributes": [
                    {"Name": "sub", "Value": str(user2)},
                    {"Name": "email", "Value": "user2@example.com"},
                    {"Name": "email_verified", "Value": "true"},
                ],
                "UserCreateDate": "2023-01-02T00:00:00Z",
                "UserLastModifiedDate": "2023-01-02T00:00:00Z",
                "Enabled": False,
                "UserStatus": "CONFIRMED",
            },
        ]
    }


@pytest.fixture
def sample_create_response():
    """Sample successful response from Cognito admin_create_user."""
    return {
        "User": {
            "Username": "test@example.com",
            "Attributes": [
                {"Name": "sub", "Value": str(uuid4())},
                {"Name": "email", "Value": "test@example.com"},
                {"Name": "email_verified", "Value": "true"},
            ],
            "UserCreateDate": "2023-01-01T00:00:00Z",
            "UserLastModifiedDate": "2023-01-01T00:00:00Z",
            "Enabled": True,
            "UserStatus": "FORCE_CHANGE_PASSWORD",
        }
    }


class TestErrorHierarchy:
    """Routers map on the base class; every specific error must be catchable as it."""

    @pytest.mark.parametrize(
        "error_cls",
        [IdentityProviderUnavailable, UserNotFoundError, UserAlreadyExistsError, InvalidIdentifierError],
    )
    def test_specific_errors_derive_from_identity_provider_error(self, error_cls):
        assert issubclass(error_cls, IdentityProviderError)


class TestProviderConstruction:
    """Client lifecycle: one lazily-built boto3 client per provider instance."""

    def test_backend_is_cognito(self, provider):
        assert provider.backend == "cognito"
        assert CognitoIdentityProvider.backend == "cognito"

    def test_client_is_not_built_at_construction(self, boto3_client_cls):
        CognitoIdentityProvider(_settings())

        boto3_client_cls.assert_not_called()

    def test_client_is_built_once_with_the_configured_region(self, boto3_client_cls, cognito_client):
        provider = CognitoIdentityProvider(_settings(region="test-west-1"))

        provider.delete_user("a@example.com")
        provider.delete_user("b@example.com")

        boto3_client_cls.assert_called_once_with("cognito-idp", region_name="test-west-1")
        assert cognito_client.admin_delete_user.call_count == 2

    def test_each_instance_builds_its_own_client(self, boto3_client_cls):
        """No module-level client cache: what used to need an autouse ``lru_cache`` reset."""
        CognitoIdentityProvider(_settings()).delete_user("a@example.com")
        CognitoIdentityProvider(_settings()).delete_user("a@example.com")

        assert boto3_client_cls.call_count == 2

    def test_describe_target_names_pool_and_region(self, provider):
        described = provider.describe_target()

        assert USER_POOL_ID in described
        assert REGION in described


class TestListUsers:
    """Tests for ``list_users`` (the paginated ``ListUsers`` walk)."""

    def test_successful_user_retrieval(self, provider, cognito_client, sample_page, cognito_logger):
        paginate = _wire_pages(cognito_client, sample_page)

        result = provider.list_users()

        assert len(result) == 2
        assert result[0].id == user1
        assert result[0].email == "user1@example.com"
        assert result[0].is_disabled is False
        assert result[1].id == user2
        assert result[1].email == "user2@example.com"
        assert result[1].is_disabled is True

        cognito_client.get_paginator.assert_called_once_with("list_users")
        paginate.assert_called_once_with(UserPoolId=USER_POOL_ID)

    def test_logs_pool_id_only_at_debug(self, provider, cognito_client, sample_page, cognito_logger):
        """Only the pool id is logged — a ``Filter`` value can carry an email or other PII."""
        _wire_pages(cognito_client, sample_page)

        provider.list_users()

        debug_text = " ".join(str(arg) for c in cognito_logger.debug.call_args_list for arg in c.args)
        assert USER_POOL_ID in debug_text
        assert "user1@example.com" not in _all_logged_text(cognito_logger)

    def test_uses_the_pool_id_from_settings(self, cognito_client, sample_page):
        paginate = _wire_pages(cognito_client, sample_page)
        provider = CognitoIdentityProvider(_settings(pool_id="custom-test-pool-id"))

        provider.list_users()

        paginate.assert_called_once_with(UserPoolId="custom-test-pool-id")

    def test_empty_users_response(self, provider, cognito_client):
        paginate = _wire_pages(cognito_client, {"Users": []})

        assert provider.list_users() == []
        paginate.assert_called_once()

    def test_response_without_users_key(self, provider, cognito_client):
        _wire_pages(cognito_client, {"NextToken": "some-token"})

        assert provider.list_users() == []

    def test_user_with_minimal_attributes_falls_back_to_username(self, provider, cognito_client):
        _wire_pages(cognito_client, {"Users": [_cognito_user(user3, "minimal@example.com")]})

        result = provider.list_users()

        assert len(result) == 1
        assert result[0].id == user3
        assert result[0].email == "minimal@example.com"
        assert result[0].is_disabled is False

    def test_user_email_fallback_to_username(self, provider, cognito_client):
        page = {
            "Users": [
                {
                    "Username": "fallback@example.com",
                    "Attributes": [{"Name": "sub", "Value": str(user1)}, {"Name": "given_name", "Value": "John"}],
                    "Enabled": True,
                }
            ]
        }
        _wire_pages(cognito_client, page)

        result = provider.list_users()

        assert len(result) == 1
        assert result[0].email == "fallback@example.com"

    def test_user_without_sub_attribute_raises(self, provider, cognito_client):
        page = {
            "Users": [
                {
                    "Username": "nosub@example.com",
                    "Attributes": [{"Name": "email", "Value": "nosub@example.com"}],
                    "Enabled": True,
                }
            ]
        }
        _wire_pages(cognito_client, page)

        with pytest.raises(ValueError):  # noqa: PT011
            provider.list_users()

    def test_user_with_no_attributes_raises(self, provider, cognito_client):
        _wire_pages(cognito_client, {"Users": [{"Username": "noattrs@example.com", "Enabled": True}]})

        with pytest.raises(ValueError):  # noqa: PT011
            provider.list_users()

    def test_invalid_uuid_in_sub_attribute_raises(self, provider, cognito_client):
        page = {
            "Users": [
                {
                    "Username": "invalid@example.com",
                    "Attributes": [
                        {"Name": "sub", "Value": "invalid-uuid-format"},
                        {"Name": "email", "Value": "invalid@example.com"},
                    ],
                    "Enabled": True,
                }
            ]
        }
        _wire_pages(cognito_client, page)

        with pytest.raises(ValueError):  # noqa: PT011
            provider.list_users()

    def test_user_enabled_status_variations(self, provider, cognito_client):
        page = {
            "Users": [
                _cognito_user(user1, "enabled@example.com", enabled=True),
                _cognito_user(user2, "disabled@example.com", enabled=False),
                # No Enabled field — must default to enabled.
                _cognito_user(user3, "no-enabled@example.com", enabled=None),
            ]
        }
        _wire_pages(cognito_client, page)

        result = provider.list_users()

        assert len(result) == 3
        assert result[0].is_disabled is False
        assert result[1].is_disabled is True
        assert result[2].is_disabled is False

    def test_complex_user_attributes(self, provider, cognito_client):
        page = {
            "Users": [
                {
                    "Username": "complex@example.com",
                    "Attributes": [
                        {"Name": "sub", "Value": str(user1)},
                        {"Name": "email", "Value": "complex@example.com"},
                        {"Name": "email_verified", "Value": "true"},
                        {"Name": "given_name", "Value": "John"},
                        {"Name": "family_name", "Value": "Doe"},
                        {"Name": "phone_number", "Value": "+1234567890"},
                        {"Name": "custom:department", "Value": "Engineering"},
                    ],
                    "UserCreateDate": "2023-01-01T00:00:00Z",
                    "UserLastModifiedDate": "2023-01-01T00:00:00Z",
                    "Enabled": True,
                    "UserStatus": "CONFIRMED",
                    "MFAOptions": [],
                }
            ]
        }
        _wire_pages(cognito_client, page)

        result = provider.list_users()

        assert len(result) == 1
        assert result[0].id == user1
        assert result[0].email == "complex@example.com"
        assert result[0].is_disabled is False

    def test_large_user_list_processing(self, provider, cognito_client):
        page = {
            "Users": [
                _cognito_user(uuid4(), f"user{i}@example.com", email=f"user{i}@example.com", enabled=i % 2 == 0)
                for i in range(100)
            ]
        }
        _wire_pages(cognito_client, page)

        result = provider.list_users()

        assert len(result) == 100
        for i, user in enumerate(result):
            assert user.is_disabled == (i % 2 != 0)
            assert user.email == f"user{i}@example.com"

    def test_paginator_consumes_all_pages(self, provider, cognito_client):
        """Cognito ListUsers returns up to 60 users per page; every page must be read.

        Silent truncation at page 1 would hide users from the admin "list users" endpoint
        and skip XNAT provisioning for anyone past the boundary.
        """
        page_1 = {"Users": [_cognito_user(user1, "page1user@example.com")]}
        page_2 = {"Users": [_cognito_user(user2, "page2user@example.com")]}
        _wire_pages(cognito_client, page_1, page_2)

        result = provider.list_users()

        assert len(result) == 2
        assert {u.id for u in result} == {user1, user2}
        cognito_client.get_paginator.assert_called_once_with("list_users")

    def test_client_error_raises_identity_provider_error(self, provider, cognito_client, cognito_logger):
        client_error = _client_error("AccessDenied", "Access denied", "ListUsers")
        cognito_client.get_paginator.return_value.paginate.side_effect = client_error

        with pytest.raises(IdentityProviderError) as exc_info:
            provider.list_users()

        # Generic message — the boto3 error string would leak request IDs / ARNs to API clients.
        assert str(exc_info.value) == "Failed to list users"
        assert str(client_error) not in str(exc_info.value)
        assert exc_info.value.__cause__ is client_error

        # Log line must carry page index + collected count so an operator can tell a
        # mid-walk throttle from a page-1 hard failure.
        cognito_logger.exception.assert_called_once()
        log_msg = cognito_logger.exception.call_args.args[0]
        assert "page=" in log_msg
        assert "collected=" in log_msg

    def test_client_error_mid_pagination_logs_page_index(self, provider, cognito_client, cognito_logger):
        """A ClientError on page 3 must log page=3 and the two users already collected."""
        page_1 = {"Users": [_cognito_user(user1, "u1@example.com")]}
        page_2 = {"Users": [_cognito_user(user2, "u2@example.com")]}

        def _paginate_three(**_params):
            yield page_1
            yield page_2
            raise _client_error("TooManyRequestsException", "Throttled", "ListUsers")

        cognito_client.get_paginator.return_value.paginate.side_effect = _paginate_three

        with pytest.raises(IdentityProviderError):
            provider.list_users()

        cognito_logger.exception.assert_called_once()
        log_msg = cognito_logger.exception.call_args.args[0]
        assert "page=3" in log_msg
        assert "collected=2" in log_msg

    @pytest.mark.parametrize(
        ("error_code", "error_message"),
        [
            ("ResourceNotFoundException", "User pool not found"),
            ("InvalidParameterException", "Invalid parameter"),
            ("TooManyRequestsException", "Rate limit exceeded"),
        ],
    )
    def test_various_client_errors(self, provider, cognito_client, cognito_logger, error_code, error_message):
        cognito_client.get_paginator.return_value.paginate.side_effect = _client_error(
            error_code, error_message, "ListUsers"
        )

        with pytest.raises(IdentityProviderError) as exc_info:
            provider.list_users()

        assert str(exc_info.value) == "Failed to list users"
        assert error_message not in str(exc_info.value)


class TestGetUser:
    """Tests for ``get_user`` — the filtered single-user lookup by email or sub."""

    def test_raises_when_no_email_or_id(self, provider, cognito_client):
        with pytest.raises(InvalidIdentifierError) as exc_info:
            provider.get_user()

        assert str(exc_info.value) == "No user email address or ID provided"
        cognito_client.get_paginator.return_value.paginate.assert_not_called()

    def test_by_email_filters_with_limit_one(self, provider, cognito_client):
        """A well-formed email produces the expected filter and reaches Cognito unmodified."""
        page = {"Users": [_cognito_user(user1, "user@example.com", email="user@example.com")]}
        paginate = _wire_pages(cognito_client, page)

        result = provider.get_user(email="user@example.com")

        assert result.id == user1
        assert result.email == "user@example.com"
        paginate.assert_called_once_with(UserPoolId=USER_POOL_ID, Filter='email = "user@example.com"', Limit=1)

    def test_by_email_does_not_log_the_filter_or_email(self, provider, cognito_client, cognito_logger):
        """The filter value is PII; it must not reach the logs at any level."""
        _wire_pages(cognito_client, {"Users": [_cognito_user(user1, "user@example.com", email="user@example.com")]})

        provider.get_user(email="user@example.com")

        assert "user@example.com" not in _all_logged_text(cognito_logger)

    def test_by_uuid_uses_canonical_form(self, provider, cognito_client):
        """A UUID-typed id yields the canonical hex+hyphen form: nothing that can break out of the quotes."""
        paginate = _wire_pages(cognito_client, {"Users": [_cognito_user(user1, "user@example.com")]})

        result = provider.get_user(user_id=user1)

        assert result.id == user1
        paginate.assert_called_once_with(UserPoolId=USER_POOL_ID, Filter=f'sub = "{user1}"', Limit=1)

    def test_by_uuid_string_is_canonicalised(self, provider, cognito_client):
        """A string id is normalised through ``UUID()`` before interpolation."""
        paginate = _wire_pages(cognito_client, {"Users": [_cognito_user(user1, "user@example.com")]})

        provider.get_user(user_id=str(user1).upper())

        paginate.assert_called_once_with(UserPoolId=USER_POOL_ID, Filter=f'sub = "{user1}"', Limit=1)

    @pytest.mark.parametrize(
        "bad_email",
        [
            'a@b.com" or email = "*',  # break out of the quoted value
            'foo"@bar.com',  # double-quote inside the local part
            '"a\\b"@example.com',  # backslash-escaped local part
            "no-at-sign",
            "user@",
            "@example.com",
            "spaces in@email.com",
        ],
    )
    def test_rejects_malformed_or_injecting_email(self, provider, cognito_client, bad_email):
        """Validate the email here, even when the caller forgets — a ``"`` in the value is what
        enables the Cognito ListUsers filter-injection payload. Reject before sending."""
        with pytest.raises(InvalidIdentifierError) as exc_info:
            provider.get_user(email=bad_email)

        assert str(exc_info.value) == "Invalid email address format"
        cognito_client.get_paginator.return_value.paginate.assert_not_called()

    def test_explicit_quote_guard_fires_if_email_validator_relaxes(self, provider, cognito_client):
        """The explicit ``"``/``\\`` rejection is belt-and-braces against a future Pydantic update
        relaxing ``EmailStr``. Bypass the validator to confirm the inner guard still catches it."""
        unsafe = 'a@b.com" or email = "*'
        with patch("flip_api.auth.identity.cognito._EMAIL_VALIDATOR.validate_python", return_value=unsafe):
            with pytest.raises(InvalidIdentifierError) as exc_info:
                provider.get_user(email=unsafe)

        assert str(exc_info.value) == "Invalid email address format"
        cognito_client.get_paginator.return_value.paginate.assert_not_called()

    @pytest.mark.parametrize(
        "bad_user_id",
        [
            'not-a-uuid" or sub = "*',  # filter-injection payload
            "123",
            "abc-def",
        ],
    )
    def test_rejects_non_uuid_user_id(self, provider, cognito_client, bad_user_id):
        """Refuse to interpolate a non-UUID into the Cognito filter."""
        with pytest.raises(InvalidIdentifierError) as exc_info:
            provider.get_user(user_id=bad_user_id)

        assert str(exc_info.value) == "Invalid user ID format"
        cognito_client.get_paginator.return_value.paginate.assert_not_called()

    def test_no_match_by_email_raises_user_not_found(self, provider, cognito_client):
        _wire_pages(cognito_client, {"Users": []})

        with pytest.raises(UserNotFoundError) as exc_info:
            provider.get_user(email="ghost@example.com")

        # Exact wording: callers depend on it.
        assert str(exc_info.value) == "User with email: ghost@example.com or ID: None is not registered."

    def test_no_match_by_id_raises_user_not_found(self, provider, cognito_client):
        _wire_pages(cognito_client, {"Users": []})

        with pytest.raises(UserNotFoundError) as exc_info:
            provider.get_user(user_id=user1)

        assert str(exc_info.value) == f"User with email: None or ID: {user1} is not registered."

    def test_client_error_propagates_from_list_users_unwrapped(self, provider, cognito_client, cognito_logger):
        """The sanitised error from the listing must surface as-is — re-wrapping through ``str(e)``
        would be the path that re-leaks Cognito internals."""
        client_error = _client_error("AccessDenied", "Access denied", "ListUsers")
        cognito_client.get_paginator.return_value.paginate.side_effect = client_error

        with pytest.raises(IdentityProviderError) as exc_info:
            provider.get_user(email="user@example.com")

        assert type(exc_info.value) is IdentityProviderError
        assert str(exc_info.value) == "Failed to list users"
        assert exc_info.value.__cause__ is client_error


class TestGetUsername:
    """``get_username`` runs on the hot path (verify_token turns a sub back into an email)."""

    def test_returns_email_for_known_sub(self, provider, cognito_client):
        page = {"Users": [_cognito_user(user1, "u@example.com", email="u@example.com")]}
        paginate = _wire_pages(cognito_client, page)

        assert provider.get_username(str(user1)) == "u@example.com"

        # The filter expression gates the query; pin it so a refactor cannot silently drop the
        # sub filter and start returning arbitrary users.
        paginate.assert_called_once_with(UserPoolId=USER_POOL_ID, Filter=f'sub = "{user1}"', Limit=1)

    def test_accepts_a_uuid_instance(self, provider, cognito_client):
        _wire_pages(cognito_client, {"Users": [_cognito_user(user1, "u@example.com", email="u@example.com")]})

        assert provider.get_username(user1) == "u@example.com"

    def test_delegates_to_get_user(self, provider):
        with patch.object(provider, "get_user", return_value=CognitoUserFactory(id=user1, email="u@example.com")):
            assert provider.get_username(user1) == "u@example.com"
            provider.get_user.assert_called_once_with(user_id=user1)

    def test_raises_user_not_found_when_no_match(self, provider, cognito_client):
        """An unknown sub means the user was deleted between token issue and this lookup —
        the caller (verify_token) converts this into a clean 401."""
        _wire_pages(cognito_client, {"Users": []})

        with pytest.raises(UserNotFoundError) as exc_info:
            provider.get_username(str(user1))

        assert "is not registered" in str(exc_info.value)
        assert str(user1) in str(exc_info.value)

    @pytest.mark.parametrize(
        "bad_user_id",
        [
            'not-a-uuid" or sub = "*',  # filter-injection payload
            "",
            "123",
            "abc-def",
        ],
    )
    def test_rejects_non_uuid_user_id(self, provider, cognito_client, bad_user_id):
        """Refuse to interpolate a non-UUID into the Cognito filter. The empty string is
        indistinguishable from "no id given" at the ``get_user`` boundary, so either
        ``InvalidIdentifierError`` wording is acceptable there."""
        with pytest.raises(InvalidIdentifierError) as exc_info:
            provider.get_username(bad_user_id)

        assert str(exc_info.value) in {"Invalid user ID format", "No user email address or ID provided"}
        cognito_client.get_paginator.return_value.paginate.assert_not_called()


class TestSetEnabled:
    """Tests for ``set_enabled`` (AdminEnableUser / AdminDisableUser)."""

    def test_disable_routes_through_admin_disable_user(self, provider, cognito_client):
        provider.set_enabled("user@example.com", False)

        cognito_client.admin_disable_user.assert_called_once_with(UserPoolId=USER_POOL_ID, Username="user@example.com")
        cognito_client.admin_enable_user.assert_not_called()

    def test_enable_routes_through_admin_enable_user(self, provider, cognito_client):
        provider.set_enabled("user@example.com", True)

        cognito_client.admin_enable_user.assert_called_once_with(UserPoolId=USER_POOL_ID, Username="user@example.com")
        cognito_client.admin_disable_user.assert_not_called()

    def test_client_error_raises_identity_provider_error(self, provider, cognito_client):
        """The ClientError text (request IDs, ARNs) is logged server-side, never echoed."""
        client_error = _client_error("UserNotFoundException", "nope", "AdminDisableUser")
        cognito_client.admin_disable_user.side_effect = client_error

        with pytest.raises(IdentityProviderError) as exc_info:
            provider.set_enabled("missing@example.com", False)

        assert str(exc_info.value) == "Failed to update user"
        assert str(client_error) not in str(exc_info.value)
        assert exc_info.value.__cause__ is client_error


class TestCreateUser:
    """Tests for ``create_user`` (AdminCreateUser)."""

    def test_successful_user_creation(self, provider, boto3_client_cls, cognito_client, sample_create_response):
        email = "test@example.com"
        expected_user_id = sample_create_response["User"]["Attributes"][0]["Value"]
        cognito_client.admin_create_user.return_value = sample_create_response

        result = provider.create_user(email)

        # The Cognito `sub` string is materialised into a UUID so the caller matches the
        # SQLModel `UserProfile.user_id` field type.
        assert result == UUID(expected_user_id)
        boto3_client_cls.assert_called_once_with("cognito-idp", region_name=REGION)
        cognito_client.admin_create_user.assert_called_once_with(
            UserPoolId=USER_POOL_ID,
            Username=email,
            UserAttributes=[{"Name": "email", "Value": email}, {"Name": "email_verified", "Value": "true"}],
        )

    def test_invite_is_sent_by_default(self, provider, cognito_client, sample_create_response):
        cognito_client.admin_create_user.return_value = sample_create_response

        provider.create_user("test@example.com")

        assert "MessageAction" not in cognito_client.admin_create_user.call_args.kwargs

    def test_suppress_invite_sets_message_action(self, provider, cognito_client, sample_create_response):
        cognito_client.admin_create_user.return_value = sample_create_response

        provider.create_user("test@example.com", suppress_invite=True)

        cognito_client.admin_create_user.assert_called_once_with(
            UserPoolId=USER_POOL_ID,
            Username="test@example.com",
            UserAttributes=[
                {"Name": "email", "Value": "test@example.com"},
                {"Name": "email_verified", "Value": "true"},
            ],
            MessageAction="SUPPRESS",
        )

    def test_user_already_exists(self, provider, cognito_client):
        email = "existing@example.com"
        client_error = _client_error("UsernameExistsException", "User already exists", "AdminCreateUser")
        cognito_client.admin_create_user.side_effect = client_error

        with pytest.raises(UserAlreadyExistsError) as exc_info:
            provider.create_user(email)

        assert str(exc_info.value) == f"User with email {email} already exists"
        assert exc_info.value.__cause__ is client_error

    def test_other_client_error(self, provider, cognito_client, cognito_logger):
        client_error = _client_error("InternalServiceError", "Internal service error", "AdminCreateUser")
        cognito_client.admin_create_user.side_effect = client_error

        with pytest.raises(IdentityProviderError) as exc_info:
            provider.create_user("test@example.com")

        assert type(exc_info.value) is IdentityProviderError
        # Generic message — boto3 ClientError text can contain request IDs and ARNs.
        assert str(exc_info.value) == "Failed to create user"
        assert str(client_error) not in str(exc_info.value)
        assert exc_info.value.__cause__ is client_error
        cognito_logger.exception.assert_called_once()

    def test_user_created_but_no_user_id(self, provider, cognito_client):
        email = "test@example.com"
        cognito_client.admin_create_user.return_value = {
            "User": {
                "Username": email,
                "Attributes": [
                    {"Name": "email", "Value": email},
                    {"Name": "email_verified", "Value": "true"},
                    # Missing 'sub' attribute
                ],
                "Enabled": True,
                "UserStatus": "FORCE_CHANGE_PASSWORD",
            }
        }

        with pytest.raises(IdentityProviderError) as exc_info:
            provider.create_user(email)

        assert str(exc_info.value) == "User created but could not get user ID"

    def test_user_created_with_empty_user_id(self, provider, cognito_client):
        email = "test@example.com"
        cognito_client.admin_create_user.return_value = {
            "User": {
                "Username": email,
                "Attributes": [
                    {"Name": "sub", "Value": ""},  # Empty user ID
                    {"Name": "email", "Value": email},
                    {"Name": "email_verified", "Value": "true"},
                ],
                "Enabled": True,
                "UserStatus": "FORCE_CHANGE_PASSWORD",
            }
        }

        with pytest.raises(IdentityProviderError) as exc_info:
            provider.create_user(email)

        assert str(exc_info.value) == "User created but could not get user ID"

    @pytest.mark.parametrize(
        "email",
        ["simple@example.com", "user.name@example.com", "user+tag@example.com", "user123@sub.example.com"],
    )
    def test_user_creation_with_different_email_formats(self, provider, cognito_client, sample_create_response, email):
        cognito_client.admin_create_user.return_value = sample_create_response

        result = provider.create_user(email)

        assert isinstance(result, UUID)
        cognito_client.admin_create_user.assert_called_once_with(
            UserPoolId=USER_POOL_ID,
            Username=email,
            UserAttributes=[{"Name": "email", "Value": email}, {"Name": "email_verified", "Value": "true"}],
        )

    def test_aws_region_usage(self, boto3_client_cls, cognito_client, sample_create_response):
        cognito_client.admin_create_user.return_value = sample_create_response
        provider = CognitoIdentityProvider(_settings(region="test-west-1"))

        provider.create_user("test@example.com")

        boto3_client_cls.assert_called_once_with("cognito-idp", region_name="test-west-1")

    def test_user_attributes_structure(self, provider, cognito_client, sample_create_response):
        email = "test@example.com"
        cognito_client.admin_create_user.return_value = sample_create_response

        provider.create_user(email)

        user_attributes = cognito_client.admin_create_user.call_args.kwargs["UserAttributes"]
        assert len(user_attributes) == 2
        assert {"Name": "email", "Value": email} in user_attributes
        assert {"Name": "email_verified", "Value": "true"} in user_attributes

    def test_multiple_attributes_in_response(self, provider, cognito_client):
        email = "test@example.com"
        expected_user_id = str(uuid4())
        cognito_client.admin_create_user.return_value = {
            "User": {
                "Username": email,
                "Attributes": [
                    {"Name": "email", "Value": email},
                    {"Name": "email_verified", "Value": "true"},
                    {"Name": "given_name", "Value": "John"},
                    {"Name": "family_name", "Value": "Doe"},
                    {"Name": "sub", "Value": expected_user_id},  # sub in the middle
                    {"Name": "phone_number", "Value": "+1234567890"},
                ],
                "Enabled": True,
                "UserStatus": "FORCE_CHANGE_PASSWORD",
            }
        }

        assert provider.create_user(email) == UUID(expected_user_id)


class TestDeleteUser:
    """Tests for ``delete_user`` (AdminDeleteUser)."""

    def test_deletes_user_successfully(self, provider, cognito_client):
        provider.delete_user("user@example.com")

        cognito_client.admin_delete_user.assert_called_once_with(UserPoolId=USER_POOL_ID, Username="user@example.com")

    def test_client_error_raises_identity_provider_error(self, provider, cognito_client):
        client_error = _client_error("ResourceNotFoundException", "gone", "AdminDeleteUser")
        cognito_client.admin_delete_user.side_effect = client_error

        with pytest.raises(IdentityProviderError) as exc_info:
            provider.delete_user("ghost@example.com")

        assert str(exc_info.value) == "Failed to delete user"
        assert str(client_error) not in str(exc_info.value)
        assert exc_info.value.__cause__ is client_error


class TestIsMfaEnabled:
    """``is_mfa_enabled``: the AdminGetUser lookup plus the base class's positive-only TTL cache."""

    USERNAME = "user@example.com"

    @staticmethod
    def _mfa_response(settings: list[str] | None) -> dict:
        response: dict = {"Username": "user@example.com"}
        if settings is not None:
            response["UserMFASettingList"] = settings
        return response

    def test_totp_enabled_returns_true(self, provider, cognito_client):
        cognito_client.admin_get_user.return_value = self._mfa_response(["SOFTWARE_TOKEN_MFA"])

        assert provider.is_mfa_enabled(self.USERNAME) is True
        cognito_client.admin_get_user.assert_called_once_with(UserPoolId=USER_POOL_ID, Username=self.USERNAME)

    def test_empty_mfa_list_returns_false(self, provider, cognito_client):
        cognito_client.admin_get_user.return_value = self._mfa_response([])

        assert provider.is_mfa_enabled(self.USERNAME) is False

    def test_missing_mfa_list_returns_false(self, provider, cognito_client):
        """A brand-new user may have no UserMFASettingList key at all."""
        cognito_client.admin_get_user.return_value = self._mfa_response(None)

        assert provider.is_mfa_enabled(self.USERNAME) is False

    def test_other_mfa_methods_do_not_count(self, provider, cognito_client):
        """Only a verified TOTP token counts; SMS MFA is not what the app-layer gate enrols."""
        cognito_client.admin_get_user.return_value = self._mfa_response(["SMS_MFA"])

        assert provider.is_mfa_enabled(self.USERNAME) is False

    def test_client_error_raises_identity_provider_error(self, provider, cognito_client, cognito_logger):
        client_error = _client_error("InternalServiceError", "boom", "AdminGetUser")
        cognito_client.admin_get_user.side_effect = client_error

        with pytest.raises(IdentityProviderError) as exc_info:
            provider.is_mfa_enabled(self.USERNAME)

        # Hot path: keep the message generic to avoid echoing Cognito/boto3 internals.
        assert str(exc_info.value) == "Failed to fetch MFA state"
        assert "boom" not in str(exc_info.value)
        assert exc_info.value.__cause__ is client_error
        cognito_logger.exception.assert_called_once()

    def test_result_is_cached_within_ttl(self, provider, cognito_client):
        """Repeat lookups inside the TTL window must not re-hit Cognito."""
        cognito_client.admin_get_user.return_value = self._mfa_response(["SOFTWARE_TOKEN_MFA"])

        assert provider.is_mfa_enabled(self.USERNAME) is True
        assert provider.is_mfa_enabled(self.USERNAME) is True
        assert provider.is_mfa_enabled(self.USERNAME) is True

        assert cognito_client.admin_get_user.call_count == 1

    def test_cached_result_expires_after_ttl(self, provider, cognito_client):
        """The positive entry lives 60 s on the monotonic clock, then the next call fetches again."""
        cognito_client.admin_get_user.return_value = self._mfa_response(["SOFTWARE_TOKEN_MFA"])

        with patch("flip_api.auth.identity.base.time.monotonic") as clock:
            clock.return_value = 1000.0
            assert provider.is_mfa_enabled(self.USERNAME) is True

            clock.return_value = 1059.0
            assert provider.is_mfa_enabled(self.USERNAME) is True
            assert cognito_client.admin_get_user.call_count == 1

            clock.return_value = 1061.0
            assert provider.is_mfa_enabled(self.USERNAME) is True
            assert cognito_client.admin_get_user.call_count == 2

    def test_negative_result_is_not_cached(self, provider, cognito_client):
        """A not-enrolled result must never be cached: first-time enrolment happens client-side
        (Amplify updateMFAPreference straight to Cognito), so the hub gets no invalidation
        signal — a cached False would 403 every MFA-gated call for up to the TTL."""
        cognito_client.admin_get_user.return_value = self._mfa_response([])

        # Pre-enrolment status check (what /users/me/mfa/status does).
        assert provider.is_mfa_enabled(self.USERNAME) is False

        # User enrols via the client; Cognito now reports an active token.
        cognito_client.admin_get_user.return_value = self._mfa_response(["SOFTWARE_TOKEN_MFA"])

        # The very next gated call must see the fresh state, not a stale False.
        assert provider.is_mfa_enabled(self.USERNAME) is True
        assert cognito_client.admin_get_user.call_count == 2

    def test_cache_is_keyed_per_username(self, provider, cognito_client):
        cognito_client.admin_get_user.return_value = self._mfa_response(["SOFTWARE_TOKEN_MFA"])

        assert provider.is_mfa_enabled("a@example.com") is True
        assert provider.is_mfa_enabled("b@example.com") is True

        assert cognito_client.admin_get_user.call_count == 2

    def test_cache_is_per_instance(self, cognito_client):
        """The cache is provider state, not module state — a fresh provider re-fetches."""
        cognito_client.admin_get_user.return_value = self._mfa_response(["SOFTWARE_TOKEN_MFA"])

        assert CognitoIdentityProvider(_settings()).is_mfa_enabled(self.USERNAME) is True
        assert CognitoIdentityProvider(_settings()).is_mfa_enabled(self.USERNAME) is True

        assert cognito_client.admin_get_user.call_count == 2

    def test_cache_is_invalidated_by_reset_mfa(self, provider, cognito_client):
        """``reset_mfa`` must drop the cached entry so the next verify_token call sees the
        fresh Cognito state."""
        cognito_client.admin_get_user.return_value = self._mfa_response(["SOFTWARE_TOKEN_MFA"])

        # Warm the cache.
        assert provider.is_mfa_enabled(self.USERNAME) is True

        provider.reset_mfa(self.USERNAME)

        # Next lookup returns no MFA and must re-hit Cognito.
        cognito_client.admin_get_user.return_value = self._mfa_response([])

        assert provider.is_mfa_enabled(self.USERNAME) is False
        assert cognito_client.admin_get_user.call_count == 2

    def test_cache_uses_the_fetch_hook(self, provider):
        """The base class caches around ``_fetch_mfa_enabled``: positive once, negative every time."""
        with patch.object(provider, "_fetch_mfa_enabled", side_effect=[False, True, True]) as fetch:
            assert provider.is_mfa_enabled(self.USERNAME) is False
            assert provider.is_mfa_enabled(self.USERNAME) is True
            assert provider.is_mfa_enabled(self.USERNAME) is True

            assert fetch.call_count == 2
            fetch.assert_called_with(self.USERNAME)


class TestResetMfa:
    """Tests for ``reset_mfa`` (clear the TOTP preference, then global sign-out)."""

    def test_successful_mfa_reset(self, provider, cognito_client):
        """Reset clears the TOTP preference AND globally signs the user out, in that order."""
        username = "user@example.com"

        provider.reset_mfa(username)

        preference_call = call.admin_set_user_mfa_preference(
            UserPoolId=USER_POOL_ID,
            Username=username,
            SoftwareTokenMfaSettings={"Enabled": False, "PreferredMfa": False},
        )
        sign_out_call = call.admin_user_global_sign_out(UserPoolId=USER_POOL_ID, Username=username)
        cognito_client.admin_set_user_mfa_preference.assert_called_once_with(**preference_call.kwargs)
        cognito_client.admin_user_global_sign_out.assert_called_once_with(**sign_out_call.kwargs)
        assert cognito_client.mock_calls.index(preference_call) < cognito_client.mock_calls.index(sign_out_call)

    def test_client_error_raises_identity_provider_error(self, provider, cognito_client, cognito_logger):
        """A ClientError on the preference step surfaces sanitised and skips the sign-out."""
        client_error = _client_error("InternalServiceError", "boom", "AdminSetUserMFAPreference")
        cognito_client.admin_set_user_mfa_preference.side_effect = client_error

        with pytest.raises(IdentityProviderError) as exc_info:
            provider.reset_mfa("user@example.com")

        assert str(exc_info.value) == "Failed to reset user MFA"
        # Never echo the underlying boto3 error text.
        assert "InternalServiceError" not in str(exc_info.value)
        assert "boom" not in str(exc_info.value)
        assert exc_info.value.__cause__ is client_error
        cognito_client.admin_user_global_sign_out.assert_not_called()
        cognito_logger.exception.assert_called_once()

    def test_sign_out_error_raises_identity_provider_error(self, provider, cognito_client):
        """If the preference clears but global sign-out fails the admin still sees a failure."""
        client_error = _client_error("InternalServiceError", "kaboom", "AdminUserGlobalSignOut")
        cognito_client.admin_user_global_sign_out.side_effect = client_error

        with pytest.raises(IdentityProviderError) as exc_info:
            provider.reset_mfa("user@example.com")

        assert str(exc_info.value) == "Failed to reset user MFA"
        assert "kaboom" not in str(exc_info.value)

    def test_calls_the_reset_hook_then_drops_the_cache(self, provider):
        with (
            patch.object(provider, "_fetch_mfa_enabled", return_value=True) as fetch,
            patch.object(provider, "_reset_mfa") as reset_hook,
        ):
            assert provider.is_mfa_enabled("user@example.com") is True
            provider.reset_mfa("user@example.com")
            assert provider.is_mfa_enabled("user@example.com") is True

            reset_hook.assert_called_once_with("user@example.com")
            assert fetch.call_count == 2


class TestFilterEnabledUsers:
    """Tests for the concrete ``filter_enabled_users`` (base class, over ``list_users``)."""

    @pytest.fixture
    def cognito_users(self):
        return [
            CognitoUserFactory(id=user1, is_disabled=False),
            CognitoUserFactory(id=user2, is_disabled=True),
            CognitoUserFactory(id=user3, is_disabled=False),
            # user4 is not in Cognito
            CognitoUserFactory(id=user5, is_disabled=False),
            CognitoUserFactory(id=user6, is_disabled=False),  # Extra user not in our input list
        ]

    def test_empty_user_list(self, provider):
        with patch.object(provider, "list_users") as list_users:
            assert provider.filter_enabled_users([]) == []

            # Should not hit the backend when there is nothing to filter.
            list_users.assert_not_called()

    def test_all_users_valid_and_enabled(self, provider, base_logger):
        valid_users = [user1, user3]
        listed = [CognitoUserFactory(id=user1, is_disabled=False), CognitoUserFactory(id=user3, is_disabled=False)]

        with patch.object(provider, "list_users", return_value=listed) as list_users:
            result = provider.filter_enabled_users(valid_users)

            assert result == valid_users
            list_users.assert_called_once_with()
            base_logger.warning.assert_not_called()

    def test_some_users_disabled(self, provider, cognito_users, base_logger):
        with patch.object(provider, "list_users", return_value=cognito_users) as list_users:
            result = provider.filter_enabled_users([user1, user2, user3])

            # user2 is disabled so should be filtered out
            assert result == [user1, user3]
            list_users.assert_called_once_with()
            base_logger.warning.assert_called_once()
            assert str(user2) in base_logger.warning.call_args.args[0]

    def test_non_existent_users(self, provider, cognito_users, base_logger):
        with patch.object(provider, "list_users", return_value=cognito_users) as list_users:
            result = provider.filter_enabled_users([user1, user4, user5])

            # user4 doesn't exist so should be filtered out
            assert result == [user1, user5]
            list_users.assert_called_once_with()
            base_logger.warning.assert_called_once()
            assert str(user4) in base_logger.warning.call_args.args[0]

    def test_mixed_user_scenarios(self, provider, cognito_users, base_logger):
        with patch.object(provider, "list_users", return_value=cognito_users) as list_users:
            result = provider.filter_enabled_users([user1, user2, user3, user4, user5])

            # user2 is disabled, user4 doesn't exist
            assert result == [user1, user3, user5]
            list_users.assert_called_once_with()

            # One warning per rejected id — user2 and user4.
            assert base_logger.warning.call_count == 2
            warning_calls = [c.args[0] for c in base_logger.warning.call_args_list]
            assert any(str(user2) in msg for msg in warning_calls)
            assert any(str(user4) in msg for msg in warning_calls)

    def test_drives_the_real_list_users(self, provider, cognito_client, sample_page):
        """End to end through the paginator: the base method and the Cognito listing agree on ids."""
        _wire_pages(cognito_client, sample_page)

        assert provider.filter_enabled_users([user1, user2]) == [user1]

    def test_error_from_list_users_propagates(self, provider):
        with patch.object(provider, "list_users", side_effect=RuntimeError("Cognito service error")):
            with pytest.raises(RuntimeError) as exc_info:
                provider.filter_enabled_users([user1])

            assert "Cognito service error" in str(exc_info.value)


class TestAllowedOrigins:
    """``allowed_origins``: the Cognito app client's CallbackURLs, normalised into a CORS allowlist."""

    def test_returns_normalized_unique_origins(self, provider, boto3_client_cls, cognito_client):
        """CallbackURLs are normalized to origins and deduplicated, preserving order."""
        cognito_client.describe_user_pool_client.return_value = {
            "UserPoolClient": {
                "CallbackURLs": [
                    "https://app.flip.aicentre.co.uk",
                    "https://localhost:443",
                    # Duplicate after normalization (default port stripped) — must be deduped.
                    "https://app.flip.aicentre.co.uk/callback",
                ]
            }
        }

        origins = provider.allowed_origins()

        assert origins == ["https://app.flip.aicentre.co.uk", "https://localhost"]
        boto3_client_cls.assert_called_once_with("cognito-idp", region_name=REGION)
        cognito_client.describe_user_pool_client.assert_called_once_with(
            UserPoolId=USER_POOL_ID, ClientId=APP_CLIENT_ID
        )

    def test_returns_empty_list_when_no_callback_urls(self, provider, cognito_client):
        cognito_client.describe_user_pool_client.return_value = {"UserPoolClient": {}}

        assert provider.allowed_origins() == []

    def test_client_error_raises_identity_provider_error(self, provider, cognito_client):
        client_error = _client_error("ResourceNotFoundException", "no such client", "DescribeUserPoolClient")
        cognito_client.describe_user_pool_client.side_effect = client_error

        with pytest.raises(IdentityProviderError) as exc_info:
            provider.allowed_origins()

        assert str(exc_info.value) == "Failed to read the allowed origins"
        assert "no such client" not in str(exc_info.value)
        assert exc_info.value.__cause__ is client_error


class TestSetPassword:
    """``set_password``: operator tooling, sets a permanent password."""

    def test_sets_a_permanent_password(self, provider, cognito_client):
        provider.set_password("user@example.com", "s3cret-value")  # pragma: allowlist secret

        cognito_client.admin_set_user_password.assert_called_once_with(
            UserPoolId=USER_POOL_ID,
            Username="user@example.com",
            Password="s3cret-value",  # pragma: allowlist secret
            Permanent=True,
        )

    def test_client_error_raises_identity_provider_error(self, provider, cognito_client):
        client_error = _client_error("InvalidPasswordException", "too weak", "AdminSetUserPassword")
        cognito_client.admin_set_user_password.side_effect = client_error

        with pytest.raises(IdentityProviderError) as exc_info:
            provider.set_password("user@example.com", "x")

        assert str(exc_info.value) == "Failed to set password"
        assert "too weak" not in str(exc_info.value)
        assert exc_info.value.__cause__ is client_error
