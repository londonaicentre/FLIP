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
from fastapi import HTTPException
from sqlmodel import Session

from flip_api.db.models.main_models import SiteBanner, SiteConfig
from flip.domain.interfaces.site import ISiteBanner, ISiteDetails
from flip.site_services.services.details_service import get_site_details, update_site_details


@pytest.fixture
def mock_db():
    return MagicMock()


def test_get_site_details_with_banner():
    # Arrange
    db = MagicMock(spec=Session)

    mock_banner = SiteBanner(
        message="Welcome!",
        link="https://example.com",
        enabled=True,
    )
    mock_config = SiteConfig(key="DeploymentMode", value=True)

    db.get.return_value = mock_banner
    db.exec.return_value = MagicMock(first=MagicMock(return_value=mock_config))

    # Act
    result = get_site_details(db)

    # Assert
    assert isinstance(result, ISiteDetails)
    assert result.banner is not None
    assert result.banner.message == "Welcome!"
    assert result.banner.link == "https://example.com"
    assert result.banner.enabled is True
    assert result.deploymentMode is True


def test_get_site_details_without_banner():
    # Arrange
    db = MagicMock(spec=Session)

    mock_config = SiteConfig(key="DeploymentMode", value=False)

    db.get.return_value = None
    db.exec.return_value = MagicMock(first=MagicMock(return_value=mock_config))

    # Act
    result = get_site_details(db)

    # Assert
    assert isinstance(result, ISiteDetails)
    assert result.banner.message == "This is a default banner message."
    assert result.deploymentMode is False


def test_update_site_details_success(mock_db):
    # Arrange
    banner = ISiteBanner(message="New Message", link="https://example.com", enabled=True)
    site_details = ISiteDetails(banner=banner, deploymentMode=False)

    existing_banner = SiteBanner(message="Old Message", link="https://old.com", enabled=False)
    existing_config = SiteConfig(key="DeploymentMode", value=True)

    mock_db.get.return_value = existing_banner
    mock_db.exec.return_value = MagicMock(first=MagicMock(return_value=existing_config))

    # Act
    result = update_site_details(site_details, mock_db)

    # Assert
    assert result is None
    assert existing_banner.message == "New Message"
    assert existing_banner.link == "https://example.com"
    assert existing_banner.enabled is True
    assert existing_config.value is False


def test_update_site_details_failure(mock_db):
    # Arrange
    banner = ISiteBanner(message="Failing Update", link="https://example.com", enabled=False)
    site_details = ISiteDetails(banner=banner, deploymentMode=False)

    # Simulate DB failure on exec
    mock_db.exec.side_effect = Exception("DB error")

    # Act & Assert
    with pytest.raises(HTTPException) as exc_info:
        update_site_details(site_details, mock_db)

    mock_db.rollback.assert_called_once()
    assert exc_info.value.status_code == 500
    assert "Error updating site details" in exc_info.value.detail
    assert "DB error" in exc_info.value.detail
