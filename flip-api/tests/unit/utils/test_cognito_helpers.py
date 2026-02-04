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

from unittest.mock import Mock, call, patch
from uuid import uuid4

import factory
import pytest
from botocore.exceptions import ClientError
from fastapi.exceptions import HTTPException
from starlette.status import HTTP_400_BAD_REQUEST, HTTP_500_INTERNAL_SERVER_ERROR

from flip_api.domain.schemas.users import CognitoUser
from flip.utils.cognito_helpers import create_cognito_user, filter_enabled_users, get_cognito_users, get_pool_id

user1, user2, user3, user4, user5, user6 = [uuid4() for i in range(6)]
USER_POOL_ID = "test-user-pool-id"
COGNITO_PARAMS = {"UserPoolId": USER_POOL_ID}


class CognitoUserFactory(factory.Factory):
    """Factory for creating CognitoUser objects."""

    class Meta:
        model = CognitoUser

    id = factory.Faker("uuid4")
    email = factory.Faker("email")
    is_disabled = factory.Faker("boolean")


@pytest.fixture
def mock_logger():
    """Mock logger for testing."""
    with patch("flip.utils.cognito_helpers.logger") as mock_logger:
        yield mock_logger


@pytest.fixture
def sample_users():
    """Create sample user IDs for testing."""
    return [user1, user2, user3, user4, user5]


@pytest.fixture
def cognito_users():
    """Create sample CognitoUser objects using the factory."""
    users = [
        CognitoUserFactory(id=user1, is_disabled=False),
        CognitoUserFactory(id=user2, is_disabled=True),
        CognitoUserFactory(id=user3, is_disabled=False),
        # user4 is not in Cognito
        CognitoUserFactory(id=user5, is_disabled=False),
        CognitoUserFactory(id=user6, is_disabled=False),  # Extra user not in our input list
    ]
    return users


class TestFilterEnabledUsers:
    """Tests for the filter_enabled_users function."""

    def test_empty_user_list(self):
        """Test that an empty user list returns an empty list."""
        with patch("flip.utils.cognito_helpers.get_cognito_users") as mock_get_users:
            result = filter_enabled_users(USER_POOL_ID, [])

            assert result == []
            # Should not call get_cognito_users if user list is empty
            mock_get_users.assert_not_called()

    def test_all_users_valid_and_enabled(self, mock_logger):
        """Test when all users exist and are enabled."""
        valid_users = [user1, user3]

        with patch("flip.utils.cognito_helpers.get_cognito_users") as mock_get_users:
            mock_get_users.return_value = [
                CognitoUserFactory(id=user1, is_disabled=False),
                CognitoUserFactory(id=user3, is_disabled=False),
            ]

            result = filter_enabled_users(USER_POOL_ID, valid_users)

            assert result == valid_users
            mock_get_users.assert_called_once_with(params=COGNITO_PARAMS)
            mock_logger.warning.assert_not_called()

    def test_some_users_disabled(self, cognito_users, mock_logger):
        """Test filtering out disabled users."""
        input_users = [user1, user2, user3]

        with patch("flip.utils.cognito_helpers.get_cognito_users") as mock_get_users:
            mock_get_users.return_value = cognito_users

            result = filter_enabled_users(USER_POOL_ID, input_users)

            # user2 is disabled so should be filtered out
            assert result == [user1, user3]
            mock_get_users.assert_called_once_with(params=COGNITO_PARAMS)
            mock_logger.warning.assert_called_once()
            assert str(user2) in mock_logger.warning.call_args[0][0]

    def test_non_existent_users(self, cognito_users, mock_logger):
        """Test filtering out users that don't exist in Cognito."""
        input_users = [user1, user4, user5]

        with patch("flip.utils.cognito_helpers.get_cognito_users") as mock_get_users:
            mock_get_users.return_value = cognito_users

            result = filter_enabled_users(USER_POOL_ID, input_users)

            # user4 doesn't exist so should be filtered out
            assert result == [user1, user5]
            mock_get_users.assert_called_once_with(params=COGNITO_PARAMS)
            mock_logger.warning.assert_called_once()
            assert str(user4) in mock_logger.warning.call_args[0][0]

    def test_mixed_user_scenarios(self, cognito_users, mock_logger):
        """Test with mix of valid, disabled, and non-existent users."""
        input_users = [user1, user2, user3, user4, user5]

        with patch("flip.utils.cognito_helpers.get_cognito_users") as mock_get_users:
            mock_get_users.return_value = cognito_users

            result = filter_enabled_users(USER_POOL_ID, input_users)

            # user2 is disabled, user4 doesn't exist
            assert result == [user1, user3, user5]
            mock_get_users.assert_called_once_with(params=COGNITO_PARAMS)

            # Warning should be called twice - once for user2 and once for user4
            assert mock_logger.warning.call_count == 2
            warning_calls = [call[0][0] for call in mock_logger.warning.call_args_list]
            assert any(str(user2) in call for call in warning_calls)
            assert any(str(user4) in call for call in warning_calls)

    def test_error_from_get_cognito_users(self):
        """Test handling of errors from get_cognito_users."""
        with patch("flip.utils.cognito_helpers.get_cognito_users") as mock_get_users:
            mock_get_users.side_effect = RuntimeError("Cognito service error")

            with pytest.raises(RuntimeError) as exc_info:
                filter_enabled_users(USER_POOL_ID, [user1])

            assert "Cognito service error" in str(exc_info.value)


class TestCreateCognitoUser:
    """Tests for the create_cognito_user function."""

    @pytest.fixture
    def mock_boto3_client(self):
        """Mock boto3 client for Cognito operations."""
        with patch("flip.utils.cognito_helpers.boto3.client") as mock_client:
            yield mock_client

    @pytest.fixture
    def mock_settings(self):
        """Mock settings for AWS region."""
        with patch("flip.utils.cognito_helpers.get_settings") as mock_get_settings:
            mock_get_settings.return_value.AWS_REGION = "eu-west-2"
            yield mock_get_settings

    @pytest.fixture
    def sample_cognito_response(self):
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

    def test_successful_user_creation(self, mock_boto3_client, mock_settings, sample_cognito_response, mock_logger):
        """Test successful user creation and ID extraction."""
        email = "test@example.com"
        user_pool_id = "test-pool-id"
        expected_user_id = sample_cognito_response["User"]["Attributes"][0]["Value"]

        # Setup mocks
        mock_client_instance = mock_boto3_client.return_value
        mock_client_instance.admin_create_user.return_value = sample_cognito_response

        # Execute
        result = create_cognito_user(email, user_pool_id)

        # Verify
        assert result == expected_user_id

        # Verify boto3 client creation
        mock_boto3_client.assert_called_once_with("cognito-idp", region_name="eu-west-2")

        # Verify admin_create_user call
        mock_client_instance.admin_create_user.assert_called_once_with(
            UserPoolId=user_pool_id,
            Username=email,
            UserAttributes=[{"Name": "email", "Value": email}, {"Name": "email_verified", "Value": "true"}],
        )

        # Verify logging
        mock_logger.debug.assert_any_call("Attempting to register the user...")
        mock_logger.debug.assert_any_call(f"Response from create user request: {sample_cognito_response}")
        mock_logger.info.assert_called_once_with("User has been created successfully")

    def test_user_already_exists(self, mock_boto3_client, mock_settings, mock_logger):
        """Test handling when user already exists."""
        email = "existing@example.com"
        user_pool_id = "test-pool-id"

        # Setup mocks
        mock_client_instance = mock_boto3_client.return_value
        client_error = ClientError(
            error_response={"Error": {"Code": "UsernameExistsException", "Message": "User already exists"}},
            operation_name="AdminCreateUser",
        )
        mock_client_instance.admin_create_user.side_effect = client_error

        # Execute and verify exception
        with pytest.raises(HTTPException) as exc_info:
            create_cognito_user(email, user_pool_id)

        assert exc_info.value.status_code == HTTP_400_BAD_REQUEST
        assert f"User with email {email} already exists" in exc_info.value.detail

    def test_other_client_error(self, mock_boto3_client, mock_settings, mock_logger):
        """Test handling of other ClientError exceptions."""
        email = "test@example.com"
        user_pool_id = "test-pool-id"
        error_message = "Internal service error"

        # Setup mocks
        mock_client_instance = mock_boto3_client.return_value
        client_error = ClientError(
            error_response={"Error": {"Code": "InternalServiceError", "Message": error_message}},
            operation_name="AdminCreateUser",
        )
        mock_client_instance.admin_create_user.side_effect = client_error

        # Execute and verify exception
        with pytest.raises(HTTPException) as exc_info:
            create_cognito_user(email, user_pool_id)

        assert exc_info.value.status_code == HTTP_500_INTERNAL_SERVER_ERROR
        assert f"Failed to create user: {str(client_error)}" in exc_info.value.detail

        # Verify logging
        mock_logger.debug.assert_called_with("Attempting to register the user...")
        mock_logger.error.assert_called_with(f"Error creating user: {str(client_error)}")

    def test_user_created_but_no_user_id(self, mock_boto3_client, mock_settings, mock_logger):
        """Test handling when user is created but user ID cannot be extracted."""
        email = "test@example.com"
        user_pool_id = "test-pool-id"

        # Response without 'sub' attribute
        response_without_sub = {
            "User": {
                "Username": email,
                "Attributes": [
                    {"Name": "email", "Value": email},
                    {"Name": "email_verified", "Value": "true"},
                    # Missing 'sub' attribute
                ],
                "UserCreateDate": "2023-01-01T00:00:00Z",
                "UserLastModifiedDate": "2023-01-01T00:00:00Z",
                "Enabled": True,
                "UserStatus": "FORCE_CHANGE_PASSWORD",
            }
        }

        # Setup mocks
        mock_client_instance = mock_boto3_client.return_value
        mock_client_instance.admin_create_user.return_value = response_without_sub

        # Execute and verify exception
        with pytest.raises(HTTPException) as exc_info:
            create_cognito_user(email, user_pool_id)

        assert exc_info.value.status_code == HTTP_500_INTERNAL_SERVER_ERROR
        assert "User created but could not get user ID" in exc_info.value.detail

        # Verify logging
        mock_logger.debug.assert_any_call("Attempting to register the user...")
        mock_logger.debug.assert_any_call(f"Response from create user request: {response_without_sub}")

    def test_user_created_with_empty_user_id(self, mock_boto3_client, mock_settings, mock_logger):
        """Test handling when user is created but user ID is empty."""
        email = "test@example.com"
        user_pool_id = "test-pool-id"

        # Response with empty 'sub' attribute
        response_empty_sub = {
            "User": {
                "Username": email,
                "Attributes": [
                    {"Name": "sub", "Value": ""},  # Empty user ID
                    {"Name": "email", "Value": email},
                    {"Name": "email_verified", "Value": "true"},
                ],
                "UserCreateDate": "2023-01-01T00:00:00Z",
                "UserLastModifiedDate": "2023-01-01T00:00:00Z",
                "Enabled": True,
                "UserStatus": "FORCE_CHANGE_PASSWORD",
            }
        }

        # Setup mocks
        mock_client_instance = mock_boto3_client.return_value
        mock_client_instance.admin_create_user.return_value = response_empty_sub

        # Execute and verify exception
        with pytest.raises(HTTPException) as exc_info:
            create_cognito_user(email, user_pool_id)

        assert exc_info.value.status_code == HTTP_500_INTERNAL_SERVER_ERROR
        assert "User created but could not get user ID" in exc_info.value.detail

    def test_user_creation_with_different_email_formats(
        self, mock_boto3_client, mock_settings, sample_cognito_response, mock_logger
    ):
        """Test user creation with various email formats."""
        test_emails = [
            "simple@example.com",
            "user.name@example.com",
            "user+tag@example.com",
            "user123@sub.example.com",
        ]
        user_pool_id = "test-pool-id"

        # Setup mocks
        mock_client_instance = mock_boto3_client.return_value
        mock_client_instance.admin_create_user.return_value = sample_cognito_response

        for email in test_emails:
            # Reset mock to track individual calls
            mock_client_instance.admin_create_user.reset_mock()

            # Execute
            result = create_cognito_user(email, user_pool_id)

            # Verify
            assert isinstance(result, str)

            # Verify correct parameters passed
            mock_client_instance.admin_create_user.assert_called_once_with(
                UserPoolId=user_pool_id,
                Username=email,
                UserAttributes=[
                    {"Name": "email", "Value": email},
                    {"Name": "email_verified", "Value": "true"},
                ],
            )

    def test_aws_region_usage(self, mock_boto3_client, mock_settings, sample_cognito_response):
        """Test that the correct AWS region is used."""
        email = "test@example.com"
        user_pool_id = "test-pool-id"
        test_region = "test-west-1"

        # Setup mocks with different region
        mock_settings.return_value.AWS_REGION = test_region
        mock_client_instance = mock_boto3_client.return_value
        mock_client_instance.admin_create_user.return_value = sample_cognito_response

        # Execute
        create_cognito_user(email, user_pool_id)

        # Verify boto3 client created with correct region
        mock_boto3_client.assert_called_once_with("cognito-idp", region_name=test_region)

    def test_user_attributes_structure(self, mock_boto3_client, mock_settings, sample_cognito_response):
        """Test that user attributes are structured correctly."""
        email = "test@example.com"
        user_pool_id = "test-pool-id"

        # Setup mocks
        mock_client_instance = mock_boto3_client.return_value
        mock_client_instance.admin_create_user.return_value = sample_cognito_response

        # Execute
        create_cognito_user(email, user_pool_id)

        # Verify the exact structure of UserAttributes
        call_args = mock_client_instance.admin_create_user.call_args
        user_attributes = call_args[1]["UserAttributes"]

        assert len(user_attributes) == 2
        assert {"Name": "email", "Value": email} in user_attributes
        assert {"Name": "email_verified", "Value": "true"} in user_attributes

    def test_multiple_attributes_in_response(self, mock_boto3_client, mock_settings, mock_logger):
        """Test user ID extraction when response has multiple attributes."""
        email = "test@example.com"
        user_pool_id = "test-pool-id"
        expected_user_id = str(uuid4())

        # Response with multiple attributes including sub
        response_multiple_attrs = {
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
                "UserCreateDate": "2023-01-01T00:00:00Z",
                "UserLastModifiedDate": "2023-01-01T00:00:00Z",
                "Enabled": True,
                "UserStatus": "FORCE_CHANGE_PASSWORD",
            }
        }

        # Setup mocks
        mock_client_instance = mock_boto3_client.return_value
        mock_client_instance.admin_create_user.return_value = response_multiple_attrs

        # Execute
        result = create_cognito_user(email, user_pool_id)

        # Verify correct user ID extracted
        assert result == expected_user_id


class TestGetPoolId:
    """Tests for the get_pool_id function."""

    @pytest.fixture
    def mock_get_settings(self):
        """Mock settings for testing."""
        with patch("flip.utils.cognito_helpers.get_settings") as mock_settings:
            yield mock_settings

    @pytest.fixture
    def mock_request(self):
        """Create a mock FastAPI request object."""
        request = Mock()
        request.state = Mock()
        return request

    def test_get_pool_id_from_environment(self, mock_get_settings, mock_request, mock_logger):
        """Test getting user pool ID from environment variable."""
        user_pool_id = "test-pool-id-from-env"
        mock_get_settings.return_value.AWS_COGNITO_USER_POOL_ID = user_pool_id

        result = get_pool_id(mock_request)

        assert result == user_pool_id
        mock_logger.debug.assert_called_with("Attempting to get the userPoolId...")
        mock_logger.info.assert_called_with(f"UserPoolId: {user_pool_id}")

    def test_get_pool_id_from_jwt_claims(self, mock_get_settings, mock_request, mock_logger):
        """Test getting user pool ID from JWT claims when not in environment."""
        user_pool_id = "test-pool-id-from-jwt"
        issuer = f"https://cognito-idp.eu-west-2.amazonaws.com/{user_pool_id}"

        # No pool ID in environment
        mock_get_settings.return_value.AWS_COGNITO_USER_POOL_ID = None

        # Setup request with auth context
        mock_request.state.auth = Mock()
        mock_request.state.auth.claims = {"iss": issuer}

        result = get_pool_id(mock_request)

        assert result == user_pool_id
        mock_logger.debug.assert_called_with("Attempting to get the userPoolId...")
        mock_logger.info.assert_called_with(f"UserPoolId: {user_pool_id}")

    def test_get_pool_id_from_jwt_claims_different_regions(self, mock_get_settings, mock_request, mock_logger):
        """Test JWT claims parsing with different AWS regions."""
        test_cases = [
            ("https://cognito-idp.us-east-1.amazonaws.com/us-east-1_ABCDEF123", "us-east-1_ABCDEF123"),
            ("https://cognito-idp.eu-west-2.amazonaws.com/eu-west-2_XYZ789", "eu-west-2_XYZ789"),
            (
                "https://cognito-idp.ap-southeast-1.amazonaws.com/ap-southeast-1_TEST123",
                "ap-southeast-1_TEST123",
            ),
        ]

        mock_get_settings.return_value.AWS_COGNITO_USER_POOL_ID = None
        mock_request.state.auth = Mock()

        for issuer, expected_pool_id in test_cases:
            mock_request.state.auth.claims = {"iss": issuer}

            result = get_pool_id(mock_request)

            assert result == expected_pool_id

    def test_get_pool_id_prefers_environment_over_jwt(self, mock_get_settings, mock_request, mock_logger):
        """Test that environment variable takes precedence over JWT claims."""
        env_pool_id = "env-pool-id"
        jwt_pool_id = "jwt-pool-id"

        mock_get_settings.return_value.AWS_COGNITO_USER_POOL_ID = env_pool_id

        # Setup JWT claims (should be ignored)
        mock_request.state.auth = Mock()
        mock_request.state.auth.claims = {"iss": f"https://cognito-idp.eu-west-2.amazonaws.com/{jwt_pool_id}"}

        result = get_pool_id(mock_request)

        assert result == env_pool_id
        mock_logger.info.assert_called_with(f"UserPoolId: {env_pool_id}")

    def test_get_pool_id_no_auth_context(self, mock_get_settings, mock_request, mock_logger):
        """Test error when no auth context and no environment variable."""
        mock_get_settings.return_value.AWS_COGNITO_USER_POOL_ID = None

        # No auth attribute on request.state
        delattr(mock_request.state, "auth") if hasattr(mock_request.state, "auth") else None

        with pytest.raises(HTTPException) as exc_info:
            get_pool_id(mock_request)

        assert exc_info.value.status_code == HTTP_500_INTERNAL_SERVER_ERROR
        assert "Token does not contain userPoolId" in exc_info.value.detail
        mock_logger.error.assert_called_with("Token does not contain userPoolId")

    def test_get_pool_id_empty_claims(self, mock_get_settings, mock_request, mock_logger):
        """Test error when auth context has empty claims."""
        mock_get_settings.return_value.AWS_COGNITO_USER_POOL_ID = None

        mock_request.state.auth = Mock()
        mock_request.state.auth.claims = {}

        with pytest.raises(HTTPException) as exc_info:
            get_pool_id(mock_request)

        assert exc_info.value.status_code == HTTP_500_INTERNAL_SERVER_ERROR
        assert "Token does not contain userPoolId" in exc_info.value.detail
        mock_logger.error.assert_called_with("Token does not contain userPoolId")

    def test_get_pool_id_no_issuer_in_claims(self, mock_get_settings, mock_request, mock_logger):
        """Test error when claims don't contain issuer."""
        mock_get_settings.return_value.AWS_COGNITO_USER_POOL_ID = None

        mock_request.state.auth = Mock()
        mock_request.state.auth.claims = {"sub": "user-123", "email": "test@example.com"}

        with pytest.raises(HTTPException) as exc_info:
            get_pool_id(mock_request)

        assert exc_info.value.status_code == HTTP_500_INTERNAL_SERVER_ERROR
        assert "Token does not contain userPoolId" in exc_info.value.detail
        mock_logger.error.assert_called_with("Token does not contain userPoolId")

    def test_get_pool_id_invalid_issuer_format(self, mock_get_settings, mock_request, mock_logger):
        """Test handling of malformed issuer URLs."""
        mock_get_settings.return_value.AWS_COGNITO_USER_POOL_ID = None

        mock_request.state.auth = Mock()
        mock_request.state.auth.claims = {"iss": "invalid-issuer-format"}

        result = get_pool_id(mock_request)

        # Should return the whole string when split fails
        assert result == "invalid-issuer-format"
        mock_logger.info.assert_called_with("UserPoolId: invalid-issuer-format")

    def test_get_pool_id_auth_without_claims(self, mock_get_settings, mock_request, mock_logger):
        """Test when auth context exists but has no claims attribute."""
        mock_get_settings.return_value.AWS_COGNITO_USER_POOL_ID = None

        mock_request.state.auth = [Mock()]
        # No claims attribute

        with pytest.raises(HTTPException) as exc_info:
            get_pool_id(mock_request)

        assert exc_info.value.status_code == HTTP_500_INTERNAL_SERVER_ERROR
        assert "Token does not contain userPoolId" in exc_info.value.detail

    def test_get_pool_id_none_claims(self, mock_get_settings, mock_request, mock_logger):
        """Test when claims is None."""
        mock_get_settings.return_value.AWS_COGNITO_USER_POOL_ID = None

        mock_request.state.auth = Mock()
        mock_request.state.auth.claims = None

        with pytest.raises(HTTPException) as exc_info:
            get_pool_id(mock_request)

        assert exc_info.value.status_code == HTTP_500_INTERNAL_SERVER_ERROR
        assert "Token does not contain userPoolId" in exc_info.value.detail

    def test_get_pool_id_empty_string_from_environment(self, mock_get_settings, mock_request, mock_logger):
        """Test when environment variable is empty string."""
        mock_get_settings.return_value.AWS_COGNITO_USER_POOL_ID = ""
        user_pool_id = "jwt-pool-id"

        # Setup JWT fallback
        mock_request.state.auth = Mock()
        mock_request.state.auth.claims = {"iss": f"https://cognito-idp.eu-west-2.amazonaws.com/{user_pool_id}"}

        result = get_pool_id(mock_request)

        assert result == user_pool_id
        mock_logger.info.assert_called_with(f"UserPoolId: {user_pool_id}")

    def test_get_pool_id_empty_issuer_value(self, mock_get_settings, mock_request, mock_logger):
        """Test when issuer claim is empty."""
        mock_get_settings.return_value.AWS_COGNITO_USER_POOL_ID = None

        mock_request.state.auth = Mock()
        mock_request.state.auth.claims = {"iss": ""}

        with pytest.raises(HTTPException) as exc_info:
            get_pool_id(mock_request)

        assert exc_info.value.status_code == HTTP_500_INTERNAL_SERVER_ERROR
        assert "Token does not contain userPoolId" in exc_info.value.detail

    def test_get_pool_id_issuer_without_amazonaws(self, mock_get_settings, mock_request, mock_logger):
        """Test issuer that doesn't contain amazonaws.com."""
        mock_get_settings.return_value.AWS_COGNITO_USER_POOL_ID = None
        issuer = "https://some-other-service.com/pool-id"

        mock_request.state.auth = Mock()
        mock_request.state.auth.claims = {"iss": issuer}

        result = get_pool_id(mock_request)

        # Should return the original issuer since split doesn't find amazonaws.com
        assert result == issuer
        mock_logger.info.assert_called_with(f"UserPoolId: {issuer}")

    def test_get_pool_id_complex_issuer_parsing(self, mock_get_settings, mock_request, mock_logger):
        """Test complex issuer URL parsing scenarios."""
        test_cases = [
            ("https://cognito-idp.eu-west-2.amazonaws.com/eu-west-2_123456789", "eu-west-2_123456789"),
            ("https://cognito-idp.us-east-1.amazonaws.com/us-east-1_ABCD/extra", "us-east-1_ABCD/extra"),
            ("cognito-idp.ap-south-1.amazonaws.com/ap-south-1_XYZ", "ap-south-1_XYZ"),
            ("amazonaws.com/simple-pool-id", "simple-pool-id"),
        ]

        mock_get_settings.return_value.AWS_COGNITO_USER_POOL_ID = None
        mock_request.state.auth = Mock()

        for issuer, expected_pool_id in test_cases:
            mock_request.state.auth.claims = {"iss": issuer}

            result = get_pool_id(mock_request)

            assert result == expected_pool_id

    def test_get_pool_id_logging_sequence(self, mock_get_settings, mock_request, mock_logger):
        """Test the complete logging sequence."""
        user_pool_id = "test-pool-id"
        mock_get_settings.return_value.AWS_COGNITO_USER_POOL_ID = user_pool_id

        get_pool_id(mock_request)

        # Verify logging calls in order
        expected_calls = [
            call("Attempting to get the userPoolId..."),
        ]
        mock_logger.debug.assert_has_calls(expected_calls)
        mock_logger.info.assert_called_once_with(f"UserPoolId: {user_pool_id}")


class TestGetCognitoUsers:
    """Tests for the get_cognito_users function."""

    @pytest.fixture
    def mock_boto3_client(self):
        """Mock boto3 client for Cognito operations."""
        with patch("flip.utils.cognito_helpers.boto3.client") as mock_client:
            yield mock_client

    @pytest.fixture
    def mock_get_settings(self):
        """Mock settings for AWS region and user pool ID."""
        with patch("flip.utils.cognito_helpers.get_settings") as mock_get_settings:
            mock_get_settings.return_value.AWS_REGION = "test-west-1"
            mock_get_settings.return_value.AWS_COGNITO_USER_POOL_ID = "test-pool-id"
            yield mock_get_settings

    @pytest.fixture
    def sample_cognito_response(self):
        """Sample response from Cognito list_users API."""
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

    def test_successful_user_retrieval_no_params(
        self, mock_boto3_client, mock_get_settings, sample_cognito_response, mock_logger
    ):
        """Test successful user retrieval with default parameters."""
        # Setup mocks
        mock_client_instance = mock_boto3_client.return_value
        mock_client_instance.list_users.return_value = sample_cognito_response

        # Execute
        result = get_cognito_users()

        # Verify
        assert len(result) == 2

        # Check first user
        assert result[0].id == user1
        assert result[0].email == "user1@example.com"
        assert result[0].is_disabled is False

        # Check second user
        assert result[1].id == user2
        assert result[1].email == "user2@example.com"
        assert result[1].is_disabled is True

        # Verify boto3 client creation
        mock_boto3_client.assert_called_once_with("cognito-idp", region_name="test-west-1")

        # Verify list_users call with default params
        expected_params = {"UserPoolId": "test-pool-id"}
        mock_client_instance.list_users.assert_called_once_with(**expected_params)

        # Verify logging
        mock_logger.debug.assert_called_with(f"Cognito list users params: {expected_params}")

    def test_custom_params_with_existing_user_pool_id(
        self, mock_boto3_client, mock_get_settings, sample_cognito_response, mock_logger
    ):
        """Test that custom UserPoolId in params is preserved."""
        custom_pool_id = "custom-pool-id"
        custom_params = {"UserPoolId": custom_pool_id, "Filter": 'email = "test@example.com"'}

        # Setup mocks
        mock_client_instance = mock_boto3_client.return_value
        mock_client_instance.list_users.return_value = sample_cognito_response

        # Execute
        get_cognito_users(params=custom_params)

        # Verify custom UserPoolId is preserved
        expected_params = {"UserPoolId": custom_pool_id, "Filter": 'email = "test@example.com"'}
        mock_client_instance.list_users.assert_called_once_with(**expected_params)

    def test_empty_users_response(self, mock_boto3_client, mock_get_settings, mock_logger):
        """Test handling of empty users response."""
        empty_response = {"Users": []}

        # Setup mocks
        mock_client_instance = mock_boto3_client.return_value
        mock_client_instance.list_users.return_value = empty_response

        # Execute
        result = get_cognito_users()

        # Verify
        assert result == []
        mock_client_instance.list_users.assert_called_once()

    def test_response_without_users_key(self, mock_boto3_client, mock_get_settings, mock_logger):
        """Test handling of response without Users key."""
        response_without_users = {"NextToken": "some-token"}

        # Setup mocks
        mock_client_instance = mock_boto3_client.return_value
        mock_client_instance.list_users.return_value = response_without_users

        # Execute
        result = get_cognito_users()

        # Verify
        assert result == []

    def test_user_with_minimal_attributes(self, mock_boto3_client, mock_get_settings, mock_logger):
        """Test parsing user with minimal attributes."""
        minimal_response = {
            "Users": [
                {
                    "Username": "minimal@example.com",
                    "Attributes": [
                        {"Name": "sub", "Value": str(user3)},
                    ],
                    "Enabled": True,
                }
            ]
        }

        # Setup mocks
        mock_client_instance = mock_boto3_client.return_value
        mock_client_instance.list_users.return_value = minimal_response

        # Execute
        result = get_cognito_users()

        # Verify
        assert len(result) == 1
        assert result[0].id == user3
        assert result[0].email == "minimal@example.com"  # Should fall back to Username
        assert result[0].is_disabled is False

    def test_user_without_sub_attribute(self, mock_boto3_client, mock_get_settings, mock_logger):
        """Test parsing user without sub attribute."""
        response_no_sub = {
            "Users": [
                {
                    "Username": "nosub@example.com",
                    "Attributes": [
                        {"Name": "email", "Value": "nosub@example.com"},
                    ],
                    "Enabled": True,
                }
            ]
        }

        # Setup mocks
        mock_client_instance = mock_boto3_client.return_value
        mock_client_instance.list_users.return_value = response_no_sub

        # Execute and verify exception
        with pytest.raises(ValueError):  # noqa: PT011
            get_cognito_users()

    def test_user_with_no_attributes(self, mock_boto3_client, mock_get_settings, mock_logger):
        """Test parsing user with no attributes."""
        response_no_attrs = {
            "Users": [
                {
                    "Username": "noattrs@example.com",
                    "Enabled": True,
                }
            ]
        }

        # Setup mocks
        mock_client_instance = mock_boto3_client.return_value
        mock_client_instance.list_users.return_value = response_no_attrs

        # Execute and verify exception
        with pytest.raises(ValueError):  # noqa: PT011
            get_cognito_users()

    def test_user_enabled_status_variations(self, mock_boto3_client, mock_get_settings, mock_logger):
        """Test different user enabled status scenarios."""
        response_enabled_variations = {
            "Users": [
                {
                    "Username": "enabled@example.com",
                    "Attributes": [{"Name": "sub", "Value": str(user1)}],
                    "Enabled": True,
                },
                {
                    "Username": "disabled@example.com",
                    "Attributes": [{"Name": "sub", "Value": str(user2)}],
                    "Enabled": False,
                },
                {
                    "Username": "no-enabled@example.com",
                    "Attributes": [{"Name": "sub", "Value": str(user3)}],
                    # No Enabled field - should default to True
                },
            ]
        }

        # Setup mocks
        mock_client_instance = mock_boto3_client.return_value
        mock_client_instance.list_users.return_value = response_enabled_variations

        # Execute
        result = get_cognito_users()

        # Verify
        assert len(result) == 3
        assert result[0].is_disabled is False  # Enabled: True
        assert result[1].is_disabled is True  # Enabled: False
        assert result[2].is_disabled is False  # No Enabled field, defaults to True

    def test_user_email_fallback_to_username(self, mock_boto3_client, mock_get_settings, mock_logger):
        """Test email fallback to username when email attribute is missing."""
        response_no_email = {
            "Users": [
                {
                    "Username": "fallback@example.com",
                    "Attributes": [
                        {"Name": "sub", "Value": str(user1)},
                        {"Name": "given_name", "Value": "John"},
                    ],
                    "Enabled": True,
                }
            ]
        }

        # Setup mocks
        mock_client_instance = mock_boto3_client.return_value
        mock_client_instance.list_users.return_value = response_no_email

        # Execute
        result = get_cognito_users()

        # Verify
        assert len(result) == 1
        assert result[0].email == "fallback@example.com"

    def test_client_error_handling(self, mock_boto3_client, mock_get_settings, mock_logger):
        """Test handling of ClientError exceptions."""
        error_message = "Access denied"

        # Setup mocks
        mock_client_instance = mock_boto3_client.return_value
        client_error = ClientError(
            error_response={"Error": {"Code": "AccessDenied", "Message": error_message}},
            operation_name="ListUsers",
        )
        mock_client_instance.list_users.side_effect = client_error

        # Execute and verify exception
        with pytest.raises(HTTPException) as exc_info:
            get_cognito_users()

        assert exc_info.value.status_code == HTTP_500_INTERNAL_SERVER_ERROR
        assert f"Failed to get Cognito users: {str(client_error)}" in exc_info.value.detail

        # Verify logging
        mock_logger.error.assert_called_with(f"Error getting Cognito users: {str(client_error)}")

    def test_various_client_errors(self, mock_boto3_client, mock_get_settings, mock_logger):
        """Test handling of various ClientError types."""
        error_scenarios = [
            ("ResourceNotFoundException", "User pool not found"),
            ("InvalidParameterException", "Invalid parameter"),
            ("TooManyRequestsException", "Rate limit exceeded"),
        ]

        mock_client_instance = mock_boto3_client.return_value

        for error_code, error_message in error_scenarios:
            client_error = ClientError(
                error_response={"Error": {"Code": error_code, "Message": error_message}},
                operation_name="ListUsers",
            )
            mock_client_instance.list_users.side_effect = client_error

            with pytest.raises(HTTPException) as exc_info:
                get_cognito_users()

            assert exc_info.value.status_code == HTTP_500_INTERNAL_SERVER_ERROR
            assert f"Failed to get Cognito users: {str(client_error)}" in exc_info.value.detail

    def test_complex_user_attributes(self, mock_boto3_client, mock_get_settings, mock_logger):
        """Test parsing user with complex attributes."""
        complex_response = {
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

        # Setup mocks
        mock_client_instance = mock_boto3_client.return_value
        mock_client_instance.list_users.return_value = complex_response

        # Execute
        result = get_cognito_users()

        # Verify
        assert len(result) == 1
        assert result[0].id == user1
        assert result[0].email == "complex@example.com"
        assert result[0].is_disabled is False

    def test_user_pool_id_from_settings(
        self, mock_boto3_client, mock_get_settings, sample_cognito_response, mock_logger
    ):
        """Test that user pool ID is correctly retrieved from settings."""
        test_pool_id = "custom-test-pool-id"
        mock_get_settings.return_value.AWS_COGNITO_USER_POOL_ID = test_pool_id

        # Setup mocks
        mock_client_instance = mock_boto3_client.return_value
        mock_client_instance.list_users.return_value = sample_cognito_response

        # Execute
        get_cognito_users()

        # Verify correct user pool ID used
        expected_params = {"UserPoolId": test_pool_id}
        mock_client_instance.list_users.assert_called_once_with(**expected_params)

    def test_invalid_uuid_in_sub_attribute(self, mock_boto3_client, mock_get_settings, mock_logger):
        """Test handling of invalid UUID in sub attribute."""
        invalid_uuid_response = {
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

        # Setup mocks
        mock_client_instance = mock_boto3_client.return_value
        mock_client_instance.list_users.return_value = invalid_uuid_response

        # Execute and verify exception
        with pytest.raises(ValueError):  # noqa: PT011
            get_cognito_users()

    def test_pagination_parameters(self, mock_boto3_client, mock_get_settings, sample_cognito_response, mock_logger):
        """Test that pagination parameters are correctly passed through."""
        pagination_params = {
            "Limit": 50,
            "PaginationToken": "eyJhbGciOiJIUzI1NiJ9...",
        }

        # Setup mocks
        mock_client_instance = mock_boto3_client.return_value
        mock_client_instance.list_users.return_value = sample_cognito_response

        # Execute
        get_cognito_users(params=pagination_params)

        # Verify pagination parameters passed through
        expected_params = {
            "UserPoolId": "test-pool-id",
            "Limit": 50,
            "PaginationToken": "eyJhbGciOiJIUzI1NiJ9...",
        }
        mock_client_instance.list_users.assert_called_once_with(**expected_params)

    def test_filter_parameters(self, mock_boto3_client, mock_get_settings, sample_cognito_response, mock_logger):
        """Test that filter parameters are correctly passed through."""
        filter_params = {
            "Filter": 'status = "Confirmed" and email_verified = "true"',
            "AttributesToGet": ["email", "email_verified", "given_name"],
        }

        # Setup mocks
        mock_client_instance = mock_boto3_client.return_value
        mock_client_instance.list_users.return_value = sample_cognito_response

        # Execute
        get_cognito_users(params=filter_params)

        # Verify filter parameters passed through
        expected_params = {
            "UserPoolId": "test-pool-id",
            "Filter": 'status = "Confirmed" and email_verified = "true"',
            "AttributesToGet": ["email", "email_verified", "given_name"],
        }
        mock_client_instance.list_users.assert_called_once_with(**expected_params)

    def test_large_user_list_processing(self, mock_boto3_client, mock_get_settings, mock_logger):
        """Test processing of large user lists."""
        # Create response with many users
        large_response = {
            "Users": [
                {
                    "Username": f"user{i}@example.com",
                    "Attributes": [
                        {"Name": "sub", "Value": str(uuid4())},
                        {"Name": "email", "Value": f"user{i}@example.com"},
                    ],
                    "Enabled": i % 2 == 0,  # Alternate enabled/disabled
                }
                for i in range(100)
            ]
        }

        # Setup mocks
        mock_client_instance = mock_boto3_client.return_value
        mock_client_instance.list_users.return_value = large_response

        # Execute
        result = get_cognito_users()

        # Verify
        assert len(result) == 100

        # Check that enabled/disabled status is correctly parsed
        for i, user in enumerate(result):
            expected_disabled = not (i % 2 == 0)  # Inverse of Enabled
            assert user.is_disabled == expected_disabled
            assert user.email == f"user{i}@example.com"

    def test_none_params_handling(self, mock_boto3_client, mock_get_settings, sample_cognito_response, mock_logger):
        """Test explicit None params handling."""
        # Setup mocks
        mock_client_instance = mock_boto3_client.return_value
        mock_client_instance.list_users.return_value = sample_cognito_response

        # Execute with explicit None
        result = get_cognito_users(params=None)

        # Verify default params are used
        expected_params = {"UserPoolId": "test-pool-id"}
        mock_client_instance.list_users.assert_called_once_with(**expected_params)
        assert len(result) == 2
