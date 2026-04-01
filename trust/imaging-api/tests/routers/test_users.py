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

from imaging_api.routers.schemas import User
from imaging_api.utils.exceptions import NotFoundError

_SAMPLE_USER = User(
    lastModified=1000,
    username="johndoe",
    enabled=True,
    id=1,
    secured=True,
    email="john.doe@hospital.nhs.uk",
    verified=True,
    firstName="John",
    lastName="Doe",
)

_SAMPLE_USER_DICT = _SAMPLE_USER.model_dump()


def test_get_users_success(client):
    with patch("imaging_api.routers.users.get_xnat_users", return_value=[_SAMPLE_USER]):
        response = client.get("/users")

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["username"] == "johndoe"


def test_create_user_success(client):
    with patch("imaging_api.routers.users.create_user", return_value=_SAMPLE_USER):
        response = client.post(
            "/users",
            json={
                "username": "johndoe",
                "password": "secret123",
                "email": "john.doe@hospital.nhs.uk",
                "firstName": "John",
                "lastName": "Doe",
            },
        )

    assert response.status_code == 200
    assert response.json()["username"] == "johndoe"


def test_create_user_failure(client):
    with patch("imaging_api.routers.users.create_user", side_effect=Exception("XNAT error")):
        response = client.post(
            "/users",
            json={
                "username": "johndoe",
                "password": "secret123",
                "email": "john.doe@hospital.nhs.uk",
                "firstName": "John",
                "lastName": "Doe",
            },
        )

    assert response.status_code == 500


def test_update_user_success(client):
    updated_user_data = _SAMPLE_USER_DICT.copy()
    updated_user_data["enabled"] = False

    with (
        patch("imaging_api.routers.users.get_user_profile_by", return_value=_SAMPLE_USER),
        patch("imaging_api.routers.users.requests.put") as mock_put,
    ):
        mock_put.return_value = MagicMock(status_code=200, json=MagicMock(return_value=updated_user_data))

        response = client.put(
            "/users",
            json={"email": "john.doe@hospital.nhs.uk", "enabled": False},
        )

    assert response.status_code == 200


def test_update_user_not_found(client):
    with patch(
        "imaging_api.routers.users.get_user_profile_by",
        side_effect=NotFoundError("User not found"),
    ):
        response = client.put(
            "/users",
            json={"email": "unknown@hospital.nhs.uk", "enabled": True},
        )

    assert response.status_code == 404


def test_update_user_not_modified(client):
    with (
        patch("imaging_api.routers.users.get_user_profile_by", return_value=_SAMPLE_USER),
        patch("imaging_api.routers.users.requests.put") as mock_put,
    ):
        mock_put.return_value = MagicMock(status_code=304)

        response = client.put(
            "/users",
            json={"email": "john.doe@hospital.nhs.uk", "enabled": True},
        )

    assert response.status_code == 200
    assert response.json()["username"] == "johndoe"


def test_update_user_put_404(client):
    with (
        patch("imaging_api.routers.users.get_user_profile_by", return_value=_SAMPLE_USER),
        patch("imaging_api.routers.users.requests.put") as mock_put,
    ):
        mock_put.return_value = MagicMock(status_code=404)

        response = client.put(
            "/users",
            json={"email": "john.doe@hospital.nhs.uk", "enabled": True},
        )

    assert response.status_code == 404


def test_update_user_put_server_error(client):
    with (
        patch("imaging_api.routers.users.get_user_profile_by", return_value=_SAMPLE_USER),
        patch("imaging_api.routers.users.requests.put") as mock_put,
    ):
        mock_put.return_value = MagicMock(status_code=500, text="Server error")

        response = client.put(
            "/users",
            json={"email": "john.doe@hospital.nhs.uk", "enabled": True},
        )

    assert response.status_code == 500


def test_add_user_to_project_success(client):
    with (
        patch("imaging_api.routers.users.get_user_profile_by", return_value=_SAMPLE_USER),
        patch("imaging_api.routers.users.add_user_to_project", return_value=_SAMPLE_USER),
    ):
        response = client.put("/users/add-to-project/johndoe/PROJ1")

    assert response.status_code == 200
    assert response.json()["username"] == "johndoe"


def test_add_user_to_project_user_not_found(client):
    with patch(
        "imaging_api.routers.users.get_user_profile_by",
        side_effect=NotFoundError("User not found"),
    ):
        response = client.put("/users/add-to-project/unknown/PROJ1")

    assert response.status_code == 404


def test_add_user_to_project_add_failure(client):
    with (
        patch("imaging_api.routers.users.get_user_profile_by", return_value=_SAMPLE_USER),
        patch(
            "imaging_api.routers.users.add_user_to_project",
            side_effect=NotFoundError("Project not found"),
        ),
    ):
        response = client.put("/users/add-to-project/johndoe/BAD_PROJ")

    assert response.status_code == 404


def test_add_user_to_project_generic_error(client):
    with (
        patch("imaging_api.routers.users.get_user_profile_by", return_value=_SAMPLE_USER),
        patch(
            "imaging_api.routers.users.add_user_to_project",
            side_effect=Exception("connection refused"),
        ),
    ):
        response = client.put("/users/add-to-project/johndoe/PROJ1")

    assert response.status_code == 500
