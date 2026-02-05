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

from unittest.mock import Mock

import pytest
from sqlmodel import Session

from flip_api.db.models.main_models import SiteBanner
from flip_api.db.seed.banner import seed_banner


@pytest.fixture
def mock_session():
    """Create a mock database session."""
    return Mock(spec=Session)


@pytest.fixture
def mock_banner():
    """Create a mock SiteBanner instance."""
    return Mock(spec=SiteBanner)


def test_seed_banner_creates_new_banner_when_none_exists(mock_session):
    """Test that seed_banner creates a new banner when none exists."""
    # Arrange
    mock_session.get.return_value = None

    # Act
    seed_banner(mock_session)

    # Assert
    mock_session.get.assert_called_once_with(SiteBanner, 1)
    mock_session.add.assert_called_once()
    mock_session.commit.assert_called_once()

    # Verify the banner properties
    added_banner = mock_session.add.call_args[0][0]
    assert isinstance(added_banner, SiteBanner)
    assert added_banner.enabled is False
    assert added_banner.message == "This message has been automatically seeded 🌱"
    assert added_banner.link == ""


def test_seed_banner_does_not_create_banner_when_exists(mock_session, mock_banner):
    """Test that seed_banner does not create a banner when one already exists."""
    # Arrange
    mock_session.get.return_value = mock_banner

    # Act
    seed_banner(mock_session)

    # Assert
    mock_session.get.assert_called_once_with(SiteBanner, 1)
    mock_session.add.assert_not_called()
    mock_session.commit.assert_not_called()


def test_seed_banner_banner_properties():
    """Test that the created banner has the correct default properties."""
    mock_session = Mock(spec=Session)
    mock_session.get.return_value = None

    seed_banner(mock_session)

    added_banner = mock_session.add.call_args[0][0]
    assert added_banner.enabled is False
    assert "automatically seeded 🌱" in added_banner.message
    assert added_banner.link == ""
