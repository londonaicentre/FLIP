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

from unittest.mock import MagicMock

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from flip_api.auth.dependencies import verify_token
from flip.db.database import get_session
from flip.db.models.main_models import SiteBanner, SiteConfig
from flip.main import app

# ---- Test setup ----


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.fixture(autouse=True)
def override_dependencies(mock_db):
    # Override the DB session
    app.dependency_overrides[get_session] = lambda: mock_db
    app.dependency_overrides[verify_token] = lambda: "test-user"

    yield

    app.dependency_overrides.clear()


# ---- Tests ----


def test_get_details_success(client, mock_db):
    # Mock the first enabled banner
    mock_banner = SiteBanner(
        message="Banner message",
        link="https://example.com/",
        enabled=True,
    )

    # Mock the deployment mode config
    mock_config = SiteConfig(
        key="DeploymentMode",
        value=True,
    )

    mock_db.get.return_value = mock_banner
    mock_db.exec.return_value = MagicMock(first=MagicMock(return_value=mock_config))

    response = client.get("/site/details")

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "banner": {"message": "Banner message", "enabled": True, "link": "https://example.com/"},
        "deploymentMode": True,
    }


def test_get_details_not_found(client, mock_db):
    mock_result = MagicMock()
    mock_result.fetchone.return_value = None
    mock_db.execute.return_value = mock_result

    response = client.get("/site/details")

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert "Error fetching site details" in response.json()["detail"]


def test_put_details_success(client, mock_db):
    # Input payload to update
    payload = {
        "banner": {"message": "Updated!", "link": "https://example.com/", "enabled": True},
        "deploymentMode": False,
    }

    # Step 1: Simulate update_site_details DB lookups
    # (e.g. SiteBanner and SiteConfig fetches during update)
    existing_banner = SiteBanner(message="Old", link="https://old.com", enabled=False)
    existing_config = SiteConfig(key="DeploymentMode", value=True)

    # Step 2: Simulate get_site_details DB fetches after update
    updated_banner = SiteBanner(message="Updated!", link="https://example.com/", enabled=True)
    updated_config = SiteConfig(key="DeploymentMode", value=False)

    # Mock DB session's call sequence:
    # 1. update_site_details: fetch existing banner (db.get)
    # 2. update_site_details: fetch existing config (db.exec)
    # 3. get_site_details: fetch updated banner (db.get)
    # 4. get_site_details: fetch updated config (db.exec)
    mock_db.get.side_effect = [
        existing_banner,  # update: banner
        updated_banner,  # get: banner
    ]
    mock_db.exec.side_effect = [
        MagicMock(first=MagicMock(return_value=existing_config)),  # update: config
        MagicMock(first=MagicMock(return_value=updated_config)),  # get: config
    ]

    response = client.put("/site/details", json=payload)

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == payload  # expecting updated result


def test_put_details_failure(client, mock_db):
    # Simulate an exception during the first DB exec (e.g., fetching banner)
    mock_db.exec.side_effect = Exception("DB failure")

    payload = {
        "banner": {"message": "Won't work", "link": "https://example.com", "enabled": True},
        "deploymentMode": False,
    }

    response = client.put("/site/details", json=payload)

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert "Error updating site details" in response.json()["detail"]
    assert "DB failure" in response.json()["detail"]
