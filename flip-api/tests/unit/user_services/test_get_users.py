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
from flip_api.db.database import get_session
from flip_api.domain.schemas.users import IUser
from flip_api.main import app

client = TestClient(app)

# ---------------------
# Fixtures
# ---------------------


@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.fixture(autouse=True)
def override_deps(mock_db):
    app.dependency_overrides[get_session] = lambda: mock_db
    app.dependency_overrides[verify_token] = lambda: uuid4()
    yield
    app.dependency_overrides.clear()


# ---------------------
# Tests
# ---------------------


class TestGetUsers:
    @patch("flip_api.user_services.get_users.get_user_role_data")
    @patch("flip_api.user_services.get_users.get_cognito_users")
    @patch("flip_api.user_services.get_users.get_total_pages")
    @patch("flip_api.user_services.get_users.get_paging_details")
    @patch("flip_api.user_services.get_users.has_permissions")
    def test_get_users_success(
        self,
        mock_has_permissions,
        mock_paging_details,
        mock_get_total,
        mock_get_cognito_users,
        mock_get_user_role_data,
    ):
        mock_has_permissions.return_value = True

        mock_paging_details.return_value = MagicMock(page_number_int=1, page_size_int=2)
        mock_get_cognito_users.return_value = ["user1", "user2"]
        mock_get_user_role_data.return_value = [
            IUser(id=uuid4(), email="user1@example.com", is_disabled=False, roles=[]),
            IUser(id=uuid4(), email="user2@example.com", is_disabled=True, roles=[]),
        ]
        mock_get_total.return_value = 1

        response = client.get("/api/users")

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        print(body)

        assert body["page"] == 1
        assert body["pageSize"] == 1
        assert body["totalPages"] == 1
        assert body["totalRecords"] == 2
        assert body["data"] == [
            user.model_dump(mode="json", by_alias=True) for user in mock_get_user_role_data.return_value
        ]

    @patch("flip_api.user_services.get_users.has_permissions")
    @patch("flip_api.user_services.get_users.get_pool_id")
    def test_get_users_no_permission(self, mock_get_user_pool_id, mock_has_permissions):
        mock_get_user_pool_id.return_value = "mock-pool"
        mock_has_permissions.return_value = False

        response = client.get("/api/users")
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "unable to manage users" in response.json()["detail"]

    @patch("flip_api.user_services.get_users.get_pool_id", side_effect=Exception("pool error"))
    @patch("flip_api.user_services.get_users.has_permissions", return_value=True)
    def test_get_users_invalid_pool(self, mock_has_permissions, mock_get_user_pool_id):
        response = client.get("/api/users")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "pool error" in response.json()["detail"]

    @patch("flip_api.user_services.get_users.get_pool_id", side_effect=Exception("server crash"))
    @patch("flip_api.user_services.get_users.has_permissions", side_effect=Exception("deep error"))
    def test_get_users_unexpected_error(self, mock_has_permissions, mock_get_user_pool_id):
        response = client.get("/api/users")
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "server crash" in response.json()["detail"] or "deep error" in response.json()["detail"]
