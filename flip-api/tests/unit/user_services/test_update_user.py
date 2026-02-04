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

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from flip_api.auth.dependencies import verify_token
from flip_api.domain.schemas.users import Disabled
from flip_api.main import app  # Ensure your FastAPI app includes the /users router
from flip_api.user_services.update_user import get_session

client = TestClient(app)

# -------------------
# Fixtures & Mocks
# -------------------


@pytest.fixture
def sample_user_id():
    return uuid4()


@pytest.fixture
def disabled_payload():
    return {"disabled": True}


@pytest.fixture
def mock_session():
    return MagicMock()


@pytest.fixture(autouse=True)
def override_dependencies(mock_session):
    app.dependency_overrides[get_session] = lambda: mock_session
    app.dependency_overrides[verify_token] = lambda: uuid4()
    yield
    app.dependency_overrides.clear()


# -------------------
# Tests
# -------------------
class TestUpdateUser:
    @patch("flip_api.user_services.update_user.update_xnat_user_profile")
    @patch("flip_api.user_services.update_user.update_user")
    @patch("flip_api.user_services.update_user.get_username")
    @patch("flip_api.user_services.update_user.has_permissions")
    def test_update_user_success(
        self,
        mock_has_permissions,
        mock_get_username,
        mock_update_user,
        mock_update_xnat,
        sample_user_id,
        disabled_payload,
    ):
        mock_has_permissions.return_value = True
        mock_get_username.return_value = "testuser@example.com"
        mock_update_user.return_value = Disabled(disabled=True)

        response = client.put(f"/users/{sample_user_id}", json=disabled_payload)
        print(response)

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"disabled": True}
        mock_update_user.assert_called_once()
        mock_update_xnat.assert_called_once()

    @patch("flip_api.user_services.update_user.has_permissions")
    def test_update_user_forbidden(self, mock_has_permissions, sample_user_id, disabled_payload):
        mock_has_permissions.return_value = False

        response = client.put(f"/users/{sample_user_id}", json=disabled_payload)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "unable to manage users" in response.json()["detail"]

    @patch("flip_api.user_services.update_user.get_username")
    @patch("flip_api.user_services.update_user.has_permissions")
    def test_update_user_not_found(self, mock_has_permissions, mock_get_username, sample_user_id, disabled_payload):
        mock_has_permissions.return_value = True
        mock_get_username.return_value = None

        response = client.put(f"/users/{sample_user_id}", json=disabled_payload)

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "could not be found" in response.json()["detail"]

    @patch("flip_api.user_services.update_user.get_username", side_effect=Exception("Unexpected error"))
    @patch("flip_api.user_services.update_user.has_permissions")
    def test_update_user_exception(self, mock_has_permissions, mock_get_username, sample_user_id, disabled_payload):
        mock_has_permissions.return_value = True

        response = client.put(f"/users/{sample_user_id}", json=disabled_payload)

        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "Unexpected error" in response.json()["detail"]
