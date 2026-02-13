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

import time
from uuid import UUID

import pytest
from sqlmodel import Session, select

from flip_api.config import get_settings
from flip_api.db.database import engine
from flip_api.db.models.user_models import Role
from tests.integration.utils import admin_authentication

REGISTER_USER_MODULE = "flip_api.user_services.register_user"


@pytest.fixture
def fetch_roles():
    """
    Fixture to fetch roles from the database.
    This is a mock for the actual database call to get roles.
    In a real scenario, you would query your database to get the roles.
    """
    # Ensure the database is initialized
    with Session(engine) as session:
        roles = session.exec(select(Role)).all()
    return [role.id for role in roles]


@pytest.fixture
def admin_auth_token():
    """
    Use boto3 to get a valid admin token for testing.
    This fixture assumes you have AWS credentials configured in your environment.
    """
    return admin_authentication()


class TestUserRegistrationRealAPI:
    """Integration tests for user registration using real authentication."""

    def test_api_connectivity(self, real_client):
        """Test basic connectivity to the API."""
        response = real_client.get(f"{get_settings().FLIP_API_URL}/docs")
        assert response.status_code == 200, "API application is not accessible"

    def test_admin_authentication_works(self, admin_auth_token: dict):
        """Test that the admin authentication token is valid."""
        assert "authorization" in admin_auth_token
        assert admin_auth_token["authorization"].startswith("Bearer ")
        print("✅ Admin authentication successful")
        print(f"Token preview: {admin_auth_token['authorization'][:50]}...")

    def test_register_user_with_project_permissions(self, client, admin_auth_token: dict, fetch_roles, user_factory):
        """
        Test registering a user with project management permissions.
        This creates a real user that can manage projects.
        """
        # User data for registration
        user_registration_data = {"email": user_factory().email, "roles": [str(role) for role in fetch_roles]}

        print(f"🚀 Attempting to register user: {user_registration_data['email']}")
        print(f"📋 With roles: {user_registration_data['roles']}")

        # Make the API call to register the user
        response = client.post("/step/users/", json=user_registration_data, headers=admin_auth_token)

        # Print response for debugging
        print(f"📡 Response status code: {response.status_code}")
        print(f"📄 Response body: {response.text}")

        # Handle different error scenarios with detailed feedback
        if response.status_code == 401:
            pytest.fail(
                "Authentication failed. The admin token may be expired or invalid. "
                f"Token used: {admin_auth_token['Authorization'][:50]}..."
            )
        elif response.status_code == 403:
            error_detail = response.json().get("detail", "No detail provided")
            pytest.fail(
                f"Admin user does not have sufficient permissions: {error_detail}. "
                "Ensure the admin user has CAN_MANAGE_USERS permission in the database."
            )
        elif response.status_code == 422:
            validation_errors = response.json().get("detail", [])
            pytest.fail(f"Validation errors in request: {validation_errors}")
        elif response.status_code == 500:
            error_detail = response.json().get("detail", "Unknown error")
            print(f"❌ Server error details: {error_detail}")
            # Don't fail immediately on 500 - let's see what the actual error is
            print("🔍 This might be a Cognito configuration issue or database connectivity problem")

        assert response.status_code == 201, f"Expected 201, got {response.status_code}: {response.text}"

        # Validate response structure
        response_data = response.json()
        required_fields = ["email", "user_id", "roles"]
        for field in required_fields:
            assert field in response_data, f"Response missing '{field}' field"

        # Validate response content
        assert response_data["email"] == user_registration_data["email"]

        # Validate user_id is a valid UUID
        try:
            user_uuid = UUID(response_data["user_id"])
            assert user_uuid is not None
        except ValueError:
            pytest.fail(f"Invalid UUID format for user_id: {response_data['user_id']}")

        print(f"✅ Successfully registered user {response_data['email']} with ID: {response_data['user_id']}")

        print(f"👤 User has roles: {response_data['roles']}")

        print("⏲️ Waiting for 5 seconds to ensure user is fully registered in the system...")
        time.sleep(5)

        print("🧹 Cleaning up: Attempting to delete the test user...")
        delete_response = client.delete(f"/users/{response_data['user_id']}", headers=admin_auth_token)
        if delete_response.status_code == 200:
            print(f"✅ Successfully deleted user {response_data['user_id']} after test.")
        else:
            print(f"⚠️ Could not delete user {response_data['user_id']}: {delete_response.text}")

        assert delete_response.status_code in [204, 200], (
            f"Expected 204 or 200 on delete, got {delete_response.status_code}: {delete_response.text}"
        )

    def test_register_user_without_admin_permissions_fails(self, real_client, fetch_roles, user_factory):
        """Test that user registration fails without proper admin permissions."""
        # Use invalid/non-admin token
        invalid_headers = {"Authorization": "Bearer invalid_token_123"}

        user_registration_data = {"email": user_factory().email, "roles": [str(role) for role in fetch_roles]}

        print("🚫 Testing registration with invalid token...")

        response = real_client.post(
            f"{get_settings().FLIP_API_URL}/users/", json=user_registration_data, headers=invalid_headers
        )

        print(f"📡 Response status code: {response.status_code}")
        print(f"📄 Response body: {response.text}")

        # Should fail with 403 Forbidden or 401 Unauthorized
        assert response.status_code in [401, 403], f"Expected 401/403, got {response.status_code}"

        if response.status_code == 403:
            error_detail = response.json().get("detail", "")
            assert "unable to register a user" in error_detail.lower()

        print("✅ Correctly rejected invalid authentication")

    def test_register_user_with_invalid_email_fails(self, real_client, admin_auth_token: dict):
        """Test that user registration fails with invalid email format."""
        user_registration_data = {"email": "invalid-email-format", "roles": ["USER"]}

        print("🚫 Testing registration with invalid email format...")

        response = real_client.post(
            f"{get_settings().FLIP_API_URL}/users/", json=user_registration_data, headers=admin_auth_token
        )

        print(f"📡 Response status code: {response.status_code}")
        print(f"📄 Response body: {response.text}")

        # Should fail with validation error (422) or bad request (400)
        assert response.status_code in [400, 422], f"Expected 400/422, got {response.status_code}"

        print("✅ Correctly rejected invalid email format")

    def test_register_user_with_invalid_roles_fails(self, real_client, admin_auth_token: dict):
        """Test that user registration fails with invalid roles."""
        user_registration_data = {
            "email": "test.invalid.roles@example.com",
            "roles": ["INVALID_ROLE", "ANOTHER_INVALID_ROLE"],
        }

        print("🚫 Testing registration with invalid roles...")

        response = real_client.post(
            f"{get_settings().FLIP_API_URL}/users/", json=user_registration_data, headers=admin_auth_token
        )

        print(f"📡 Response status code: {response.status_code}")
        print(f"📄 Response body: {response.text}")

        # Should fail with bad request or validation error
        assert response.status_code in [400, 422, 500], f"Expected error status, got {response.status_code}"

        print("✅ Correctly rejected invalid roles")


@pytest.mark.skip
class TestSpecificUserCreation:
    """Specific test for creating rafaelagd@gmail.com user."""

    def test_create_rafaelagd_user_with_project_permissions(self, real_client, admin_auth_token: dict, fetch_roles):
        """
        Specifically test creating rafaelagd@gmail.com with project management permissions.
        """
        # Specific user data for rafaelagd@gmail.com
        user_registration_data = {
            "email": "rafaelagd@gmail.com",
            "roles": [str(role) for role in fetch_roles if "PROJECT" in str(role) or "USER" in str(role)],
        }

        # If no project-related roles found, use all available roles
        if not user_registration_data["roles"]:
            user_registration_data["roles"] = [str(role) for role in fetch_roles]

        print(f"🎯 Creating specific user: {user_registration_data['email']}")
        print(f"📋 With roles: {user_registration_data['roles']}")

        # Make the API call to register the user
        response = real_client.post(
            f"{get_settings().FLIP_API_URL}/users/", json=user_registration_data, headers=admin_auth_token
        )

        print(f"📡 Response status code: {response.status_code}")
        print(f"📄 Response body: {response.text}")

        if response.status_code != 200:
            error_detail = response.json().get("detail", "Unknown error")
            pytest.fail(f"Failed to create rafaelagd@gmail.com user: {error_detail}")

        response_data = response.json()

        # Validate the specific user was created
        assert response_data["email"] == "rafaelagd@gmail.com"
        assert "user_id" in response_data

        print(f"🎉 Successfully created rafaelagd@gmail.com with ID: {response_data['user_id']}")
        print(f"👤 User has roles: {response_data['roles']}")

        return response_data
