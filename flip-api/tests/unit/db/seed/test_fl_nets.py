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
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from sqlmodel import Session

from flip_api.db.models.main_models import FLNets
from flip_api.db.seed.fl_nets import seed_fl_nets
from flip_api.domain.schemas.types import FLBackend


@pytest.fixture
def mock_session():
    """Create a mock database session."""
    return Mock(spec=Session)


@pytest.fixture
def mock_fl_net():
    """Create a mock FLNets instance with a backend already set (no backfill needed)."""
    net = Mock(spec=FLNets)
    net.name = "existing_net"
    net.endpoint = "http://existing.com"
    net.fl_backend = FLBackend.FLOWER
    return net


@pytest.fixture
def sample_net_endpoints():
    """Sample NET_ENDPOINTS dict for testing."""
    return {"net1": "http://net1.com", "net2": "http://net2.com"}


def _mock_settings(net_endpoints: dict, fl_backend: FLBackend = FLBackend.NVFLARE) -> SimpleNamespace:
    """Helper to build a settings-like object."""
    return SimpleNamespace(NET_ENDPOINTS=net_endpoints, FL_BACKEND=fl_backend)


@patch("flip_api.db.seed.fl_nets.get_settings")
def test_seed_fl_nets_creates_new_nets_when_none_exist(mock_get_settings, mock_session, sample_net_endpoints):
    """Test that seed_fl_nets creates new nets when none exist."""
    mock_get_settings.return_value = _mock_settings(sample_net_endpoints)
    mock_session.exec.return_value.all.side_effect = [[], []]  # initial read + final return

    seed_fl_nets(mock_session)

    mock_get_settings.assert_called_once()
    assert mock_session.add.call_count == 2  # Two new nets added
    mock_session.commit.assert_called_once()  # Single commit at end of upsert pass

    added_net1, added_net2 = (call.args[0] for call in mock_session.add.call_args_list)
    assert isinstance(added_net1, FLNets)
    assert isinstance(added_net2, FLNets)
    assert {added_net1.name, added_net2.name} == {"net1", "net2"}
    assert {added_net1.endpoint, added_net2.endpoint} == {"http://net1.com", "http://net2.com"}
    # New rows are bootstrapped with the declared FL_BACKEND.
    assert added_net1.fl_backend == FLBackend.NVFLARE
    assert added_net2.fl_backend == FLBackend.NVFLARE


@patch("flip_api.db.seed.fl_nets.get_settings")
def test_seed_fl_nets_skips_matching_existing_nets(mock_get_settings, mock_session, mock_fl_net):
    """Existing row whose endpoint and backend already match is left alone."""
    mock_get_settings.return_value = _mock_settings(
        {"existing_net": "http://existing.com", "new_net": "http://new.com"}, fl_backend=FLBackend.FLOWER
    )
    mock_session.exec.return_value.all.side_effect = [[mock_fl_net], [mock_fl_net, Mock(spec=FLNets)]]

    seed_fl_nets(mock_session)

    mock_session.add.assert_called_once()  # Only the new net is added; the matching one is untouched
    added_net = mock_session.add.call_args.args[0]
    assert added_net.name == "new_net"
    assert added_net.endpoint == "http://new.com"


@patch("flip_api.db.seed.fl_nets.get_settings")
def test_seed_fl_nets_reconciles_stale_endpoint(mock_get_settings, mock_session, mock_fl_net):
    """Regression: an existing row whose endpoint differs from NET_ENDPOINTS gets reconciled.

    Before the upsert refactor, `seed_fl_nets` was insert-only — once a row existed,
    its endpoint was never updated. After the EC2-compose → ECS Cloud Map cutover,
    rows seeded with the old docker-compose hostname stranded `/api/fl/status` with
    `Name or service not known`.
    """
    mock_get_settings.return_value = _mock_settings(
        {"existing_net": "http://fl-api-net-1.flip.local:8000"}, fl_backend=FLBackend.FLOWER
    )
    mock_session.exec.return_value.all.side_effect = [[mock_fl_net], [mock_fl_net]]

    seed_fl_nets(mock_session)

    assert mock_fl_net.endpoint == "http://fl-api-net-1.flip.local:8000"
    mock_session.add.assert_called_once_with(mock_fl_net)
    mock_session.commit.assert_called_once()


@patch("flip_api.db.seed.fl_nets.get_settings")
def test_seed_fl_nets_returns_all_nets(mock_get_settings, mock_session, sample_net_endpoints):
    """Test that seed_fl_nets returns all nets from database."""
    mock_get_settings.return_value = _mock_settings(sample_net_endpoints)
    final_nets = [Mock(spec=FLNets), Mock(spec=FLNets)]
    mock_session.exec.return_value.all.side_effect = [[], final_nets]

    result = seed_fl_nets(mock_session)

    assert result == final_nets


@patch("flip_api.db.seed.fl_nets.get_settings")
def test_seed_fl_nets_with_empty_endpoints(mock_get_settings, mock_session):
    """Empty NET_ENDPOINTS: no add() calls; commit() is still invoked once (no-op transaction)."""
    mock_get_settings.return_value = _mock_settings({})
    mock_session.exec.return_value.all.side_effect = [[], []]

    result = seed_fl_nets(mock_session)

    mock_session.add.assert_not_called()
    assert result == []


@patch("flip_api.db.seed.fl_nets.get_settings")
def test_seed_fl_nets_backfills_null_backend(mock_get_settings, mock_session):
    """A pre-existing row with fl_backend=None (created before the column existed) is set from FL_BACKEND."""
    legacy = Mock(spec=FLNets)
    legacy.name = "existing_net"
    legacy.endpoint = "http://existing.com"
    legacy.fl_backend = None
    mock_get_settings.return_value = _mock_settings(
        {"existing_net": "http://existing.com"}, fl_backend=FLBackend.NVFLARE
    )
    mock_session.exec.return_value.all.side_effect = [[legacy], [legacy]]

    seed_fl_nets(mock_session)

    assert legacy.fl_backend == FLBackend.NVFLARE
    mock_session.add.assert_called_once_with(legacy)


@patch("flip_api.db.seed.fl_nets.get_settings")
def test_seed_fl_nets_overwrites_existing_backend(mock_get_settings, mock_session, mock_fl_net):
    """FL_BACKEND is canonical: an existing row's backend is overwritten on every seed.

    The mock net already matches endpoint and has fl_backend='flower'; seeding with FL_BACKEND='nvflare'
    must overwrite it (this is how `make restart-fl FL_BACKEND=...` switches frameworks).
    """
    mock_get_settings.return_value = _mock_settings(
        {"existing_net": "http://existing.com"}, fl_backend=FLBackend.NVFLARE
    )
    mock_session.exec.return_value.all.side_effect = [[mock_fl_net], [mock_fl_net]]

    seed_fl_nets(mock_session)

    assert mock_fl_net.fl_backend == FLBackend.NVFLARE
    mock_session.add.assert_called_once_with(mock_fl_net)
