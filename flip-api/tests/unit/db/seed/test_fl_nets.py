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

from unittest.mock import Mock, patch

import pytest
from sqlmodel import Session

from flip_api.db.models.main_models import FLNets
from flip.db.seed.fl_nets import seed_fl_nets


@pytest.fixture
def mock_session():
    """Create a mock database session."""
    return Mock(spec=Session)


@pytest.fixture
def mock_fl_net():
    """Create a mock FLNets instance."""
    net = Mock(spec=FLNets)
    net.name = "existing_net"
    net.endpoint = "http://existing.com"
    return net


@pytest.fixture
def sample_secret():
    """Sample secrets data for testing."""
    return "{'net1': 'http://net1.com', 'net2': 'http://net2.com'}"


@patch("flip.db.seed.fl_nets.get_secret")
def test_seed_fl_nets_creates_new_nets_when_none_exist(mock_get_secret, mock_session, sample_secret):
    """Test that seed_fl_nets creates new nets when none exist."""
    # Arrange
    mock_get_secret.return_value = sample_secret
    mock_session.exec.return_value.all.side_effect = [[], []]  # First call empty, second call empty for return

    # Act
    seed_fl_nets(mock_session)

    # Assert
    mock_get_secret.assert_called_once()
    assert mock_session.exec.call_count == 2  # Once to check existing, once to return all
    assert mock_session.add.call_count == 2  # Two nets added
    assert mock_session.commit.call_count == 2  # Two commits

    # Verify the nets that were added
    add_calls = mock_session.add.call_args_list
    added_net1 = add_calls[0][0][0]
    added_net2 = add_calls[1][0][0]

    assert isinstance(added_net1, FLNets)
    assert isinstance(added_net2, FLNets)
    assert {added_net1.name, added_net2.name} == {"net1", "net2"}
    assert {added_net1.endpoint, added_net2.endpoint} == {"http://net1.com", "http://net2.com"}


@patch("flip.db.seed.fl_nets.get_secret")
def test_seed_fl_nets_skips_existing_nets(mock_get_secret, mock_session, sample_secret, mock_fl_net):
    """Test that seed_fl_nets skips existing nets and only adds new ones."""
    # Arrange
    sample_secret = "{'existing_net': 'http://existing.com', 'new_net': 'http://new.com'}"
    mock_get_secret.return_value = sample_secret

    existing_nets = [mock_fl_net]
    final_nets = [mock_fl_net, Mock(spec=FLNets)]
    mock_session.exec.return_value.all.side_effect = [existing_nets, final_nets]

    # Act
    seed_fl_nets(mock_session)

    # Assert
    mock_session.add.assert_called_once()  # Only one new net added
    mock_session.commit.assert_called_once()

    # Verify only the new net was added
    added_net = mock_session.add.call_args[0][0]
    assert added_net.name == "new_net"
    assert added_net.endpoint == "http://new.com"


@patch("flip.db.seed.fl_nets.get_secret")
def test_seed_fl_nets_handles_json_with_single_quotes(mock_get_secret, mock_session):
    """Test that seed_fl_nets correctly handles JSON with single quotes."""
    # Arrange
    secrets_with_single_quotes = "{'test_net': 'http://test.com'}"
    mock_get_secret.return_value = secrets_with_single_quotes
    mock_session.exec.return_value.all.side_effect = [[], []]

    # Act
    seed_fl_nets(mock_session)

    # Assert
    mock_session.add.assert_called_once()
    added_net = mock_session.add.call_args[0][0]
    assert added_net.name == "test_net"
    assert added_net.endpoint == "http://test.com"


@patch("flip.db.seed.fl_nets.get_secret")
def test_seed_fl_nets_returns_all_nets(mock_get_secret, mock_session, sample_secret):
    """Test that seed_fl_nets returns all nets from database."""
    # Arrange
    mock_get_secret.return_value = sample_secret
    mock_net1 = Mock(spec=FLNets)
    mock_net2 = Mock(spec=FLNets)
    final_nets = [mock_net1, mock_net2]

    mock_session.exec.return_value.all.side_effect = [[], final_nets]

    # Act
    result = seed_fl_nets(mock_session)

    # Assert
    assert isinstance(result, list)
    assert len(result) == 2
    assert result == final_nets


@patch("flip.db.seed.fl_nets.get_secret")
def test_seed_fl_nets_with_empty_secrets(mock_get_secret, mock_session):
    """Test that seed_fl_nets handles empty net endpoints."""
    # Arrange
    empty_secrets = "{}"
    mock_get_secret.return_value = empty_secrets
    mock_session.exec.return_value.all.return_value = []

    # Act
    result = seed_fl_nets(mock_session)

    # Assert
    mock_session.add.assert_not_called()
    mock_session.commit.assert_not_called()
    assert result == []
