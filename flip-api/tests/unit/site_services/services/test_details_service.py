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
from flip_api.domain.interfaces.site import ISiteBanner, ISiteDetails
from flip_api.site_services.services.details_service import get_site_details, update_site_details


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
    # HttpUrl normalises a bare authority by appending the root path, so the round-tripped value
    # gains a trailing slash. Equivalent URL, deliberate consequence of validating the scheme.
    assert str(result.banner.link) == "https://example.com/"
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
    assert existing_banner.link == "https://example.com/"
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
    # The raw exception text must not reach the client (the #888 class of leak).
    assert exc_info.value.detail == "Internal server error"
    assert "DB error" not in exc_info.value.detail


def test_get_site_details_no_deployment_config():
    """When DeploymentMode config is missing, should raise HTTPException 404."""
    db = MagicMock(spec=Session)

    mock_banner = SiteBanner(message="Hello", link="https://example.com", enabled=True)
    db.get.return_value = mock_banner
    db.exec.return_value = MagicMock(first=MagicMock(return_value=None))

    with pytest.raises(HTTPException) as exc_info:
        get_site_details(db)

    assert exc_info.value.status_code == 404
    assert "Deployment mode not found" in exc_info.value.detail


def test_get_site_details_banner_with_empty_link():
    """When banner link is an empty string, returned link should be None."""
    db = MagicMock(spec=Session)

    mock_banner = SiteBanner(message="Hello", link="", enabled=True)
    mock_config = SiteConfig(key="DeploymentMode", value=True)

    db.get.return_value = mock_banner
    db.exec.return_value = MagicMock(first=MagicMock(return_value=mock_config))

    result = get_site_details(db)

    assert result.banner is not None
    assert result.banner.link is None
    assert result.banner.message == "Hello"


def test_get_site_details_banner_with_whitespace_link():
    """When banner link is whitespace-only, returned link should be None."""
    db = MagicMock(spec=Session)

    mock_banner = SiteBanner(message="Hello", link="   ", enabled=True)
    mock_config = SiteConfig(key="DeploymentMode", value=False)

    db.get.return_value = mock_banner
    db.exec.return_value = MagicMock(first=MagicMock(return_value=mock_config))

    result = get_site_details(db)

    assert result.banner is not None
    assert result.banner.link is None


def test_get_site_details_degrades_a_poisoned_link_to_none():
    """A stored banner whose link is not http(s) must serve with link=None, not 500.

    The scheme allowlist lives on ``ISiteBanner.link`` (HttpUrl), so a row written before that
    validator existed — or by any path that bypassed it — fails validation on read. Every page
    load reads the banner, so the read path must degrade rather than turn one poisoned row into a
    site-wide outage. The rest of the banner (message, enabled) is preserved.
    """
    db = MagicMock(spec=Session)

    # A plain str column accepts this; it is exactly the value the frontend guard exists to stop.
    poisoned_banner = SiteBanner(message="Hello", link="javascript:alert(1)", enabled=True)
    mock_config = SiteConfig(key="DeploymentMode", value=True)

    db.get.return_value = poisoned_banner
    db.exec.return_value = MagicMock(first=MagicMock(return_value=mock_config))

    result = get_site_details(db)

    assert result.banner is not None
    assert result.banner.link is None
    assert result.banner.message == "Hello"
    assert result.banner.enabled is True


def test_get_site_details_serves_a_default_when_the_banner_is_unusable_without_its_link():
    """A row invalid for a reason other than the link still must not 500 the whole site.

    Dropping the link is the common degrade, but if the stored row is invalid in some other field
    (e.g. a legacy NULL message), nulling the link is not enough and re-validating would raise. The
    read path must fall back to a safe, disabled default rather than propagate a 500 to every page.
    """
    db = MagicMock(spec=Session)

    # model_dump is what the read path validates; inject a row that fails on a non-link field.
    unusable_row = MagicMock()
    unusable_row.model_dump.return_value = {"message": None, "link": "https://example.com", "enabled": True}
    mock_config = SiteConfig(key="DeploymentMode", value=False)

    db.get.return_value = unusable_row
    db.exec.return_value = MagicMock(first=MagicMock(return_value=mock_config))

    result = get_site_details(db)

    assert result.banner is not None
    assert result.banner.link is None
    assert result.banner.enabled is False


def test_update_site_details_creates_new_banner(mock_db):
    """When no existing banner, should create a new SiteBanner and call db.add."""
    banner = ISiteBanner(message="Brand New", link="https://new.com", enabled=True)
    site_details = ISiteDetails(banner=banner, deploymentMode=True)

    existing_config = SiteConfig(key="DeploymentMode", value=False)

    mock_db.get.return_value = None  # No existing banner
    mock_db.exec.return_value = MagicMock(first=MagicMock(return_value=existing_config))

    update_site_details(site_details, mock_db)

    mock_db.add.assert_called_once()
    added_banner = mock_db.add.call_args[0][0]
    assert isinstance(added_banner, SiteBanner)
    assert added_banner.message == "Brand New"
    assert added_banner.link == "https://new.com/"
    assert added_banner.enabled is True


def test_update_site_details_creates_new_config(mock_db):
    """When no existing DeploymentMode config, should create a new SiteConfig and call db.add."""
    banner = ISiteBanner(message="Hello", link="https://example.com", enabled=True)
    site_details = ISiteDetails(banner=banner, deploymentMode=True)

    existing_banner = SiteBanner(message="Old", link="https://old.com", enabled=False)

    mock_db.get.return_value = existing_banner
    mock_db.exec.return_value = MagicMock(first=MagicMock(return_value=None))  # No existing config

    update_site_details(site_details, mock_db)

    mock_db.add.assert_called_once()
    added_config = mock_db.add.call_args[0][0]
    assert isinstance(added_config, SiteConfig)
    assert added_config.key == "DeploymentMode"
    assert added_config.value is True


def test_update_site_details_no_banner_in_payload(mock_db):
    """When banner is None in payload, should skip banner update and only commit once (for config)."""
    site_details = ISiteDetails(banner=None, deploymentMode=False)

    existing_config = SiteConfig(key="DeploymentMode", value=True)

    mock_db.get.return_value = SiteBanner(message="Existing", link="https://old.com", enabled=True)
    mock_db.exec.return_value = MagicMock(first=MagicMock(return_value=existing_config))

    update_site_details(site_details, mock_db)

    assert mock_db.commit.call_count == 1  # Only config commit, no banner commit
    assert existing_config.value is False


def test_update_site_details_banner_with_none_link(mock_db):
    """When banner link is None, stored link should be empty string."""
    banner = ISiteBanner(message="No Link", link=None, enabled=True)
    site_details = ISiteDetails(banner=banner, deploymentMode=True)

    existing_banner = SiteBanner(message="Old", link="https://old.com", enabled=False)
    existing_config = SiteConfig(key="DeploymentMode", value=False)

    mock_db.get.return_value = existing_banner
    mock_db.exec.return_value = MagicMock(first=MagicMock(return_value=existing_config))

    update_site_details(site_details, mock_db)

    assert existing_banner.link == ""
