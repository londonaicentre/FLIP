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

import pytest
from sqlmodel import Session

from flip_api.db.models.main_models import FLNets, FLScheduler
from flip.db.seed.fl_scheduler import seed_fl_scheduler
from flip.domain.schemas.status import NetStatus


@pytest.fixture
def mock_session():
    """Create a mock session for testing."""
    session = MagicMock(spec=Session)
    return session


@pytest.fixture
def sample_nets():
    """Create sample FLNets for testing."""
    net1 = MagicMock(spec=FLNets)
    net1.id = 1
    net1.name = "test_net_1"

    net2 = MagicMock(spec=FLNets)
    net2.id = 2
    net2.name = "test_net_2"

    net3 = MagicMock(spec=FLNets)
    net3.id = 3
    net3.name = "test_net_3"

    return [net1, net2, net3]


@pytest.fixture
def sample_existing_scheduler():
    """Create a sample existing scheduler."""
    scheduler = MagicMock(spec=FLScheduler)
    scheduler.id = 1
    scheduler.netid = 1
    scheduler.status = NetStatus.AVAILABLE
    return scheduler


@pytest.fixture
def sample_all_schedulers():
    """Create sample schedulers that would be returned."""
    scheduler1 = MagicMock(spec=FLScheduler)
    scheduler1.id = 1
    scheduler1.netid = 1
    scheduler1.status = NetStatus.AVAILABLE

    scheduler2 = MagicMock(spec=FLScheduler)
    scheduler2.id = 2
    scheduler2.netid = 2
    scheduler2.status = NetStatus.AVAILABLE

    scheduler3 = MagicMock(spec=FLScheduler)
    scheduler3.id = 3
    scheduler3.netid = 3
    scheduler3.status = NetStatus.AVAILABLE

    return [scheduler1, scheduler2, scheduler3]


@patch("flip.db.seed.fl_scheduler.logger")
@patch("flip.db.seed.fl_scheduler.select")
@patch("flip.db.seed.fl_scheduler.col")
@patch("flip.db.seed.fl_scheduler.FLScheduler")
def test_seed_fl_scheduler_no_existing_schedulers(
    mock_fl_scheduler_class, mock_col, mock_select, mock_logger, mock_session, sample_nets, sample_all_schedulers
):
    """Test seeding when no existing schedulers exist."""
    # Setup mocks
    mock_session.exec.side_effect = [
        MagicMock(first=MagicMock(return_value=None)),  # No existing scheduler for net1
        MagicMock(first=MagicMock(return_value=None)),  # No existing scheduler for net2
        MagicMock(first=MagicMock(return_value=None)),  # No existing scheduler for net3
        MagicMock(all=MagicMock(return_value=sample_all_schedulers)),  # Return all schedulers
    ]

    mock_new_schedulers = [MagicMock(), MagicMock(), MagicMock()]
    mock_fl_scheduler_class.side_effect = mock_new_schedulers

    # Execute
    result = seed_fl_scheduler(mock_session, sample_nets)

    # Verify
    assert mock_session.exec.call_count == 4  # 3 checks + 1 final select
    assert mock_session.add.call_count == 3  # 3 new schedulers added
    assert mock_session.commit.call_count == 1
    assert mock_fl_scheduler_class.call_count == 3

    # Verify new schedulers created with correct parameters
    for i, net in enumerate(sample_nets):
        mock_fl_scheduler_class.assert_any_call(status=NetStatus.AVAILABLE, netid=net.id)
        mock_session.add.assert_any_call(mock_new_schedulers[i])

    assert result == sample_all_schedulers
    assert len(result) == 3


@patch("flip.db.seed.fl_scheduler.logger")
@patch("flip.db.seed.fl_scheduler.select")
@patch("flip.db.seed.fl_scheduler.col")
@patch("flip.db.seed.fl_scheduler.FLScheduler")
def test_seed_fl_scheduler_some_existing_schedulers(
    mock_fl_scheduler_class,
    mock_col,
    mock_select,
    mock_logger,
    mock_session,
    sample_nets,
    sample_existing_scheduler,
    sample_all_schedulers,
):
    """Test seeding when some schedulers already exist."""
    # Setup mocks - first net has existing scheduler, others don't
    mock_session.exec.side_effect = [
        MagicMock(first=MagicMock(return_value=sample_existing_scheduler)),  # Existing for net1
        MagicMock(first=MagicMock(return_value=None)),  # No existing for net2
        MagicMock(first=MagicMock(return_value=None)),  # No existing for net3
        MagicMock(all=MagicMock(return_value=sample_all_schedulers)),  # Return all schedulers
    ]

    mock_new_schedulers = [MagicMock(), MagicMock()]
    mock_fl_scheduler_class.side_effect = mock_new_schedulers

    # Execute
    result = seed_fl_scheduler(mock_session, sample_nets)

    # Verify
    assert mock_session.exec.call_count == 4  # 3 checks + 1 final select
    assert mock_session.add.call_count == 2  # Only 2 new schedulers added
    assert mock_session.commit.call_count == 1
    assert mock_fl_scheduler_class.call_count == 2  # Only 2 new schedulers created

    assert result == sample_all_schedulers


@patch("flip.db.seed.fl_scheduler.logger")
@patch("flip.db.seed.fl_scheduler.select")
@patch("flip.db.seed.fl_scheduler.col")
@patch("flip.db.seed.fl_scheduler.FLScheduler")
def test_seed_fl_scheduler_all_existing_schedulers(
    mock_fl_scheduler_class,
    mock_col,
    mock_select,
    mock_logger,
    mock_session,
    sample_nets,
    sample_existing_scheduler,
    sample_all_schedulers,
):
    """Test seeding when all schedulers already exist."""
    # Setup mocks - all nets have existing schedulers
    mock_session.exec.side_effect = [
        MagicMock(first=MagicMock(return_value=sample_existing_scheduler)),  # Existing for net1
        MagicMock(first=MagicMock(return_value=sample_existing_scheduler)),  # Existing for net2
        MagicMock(first=MagicMock(return_value=sample_existing_scheduler)),  # Existing for net3
        MagicMock(all=MagicMock(return_value=sample_all_schedulers)),  # Return all schedulers
    ]

    # Execute
    result = seed_fl_scheduler(mock_session, sample_nets)

    # Verify
    assert mock_session.exec.call_count == 4  # 3 checks + 1 final select
    assert mock_session.add.call_count == 0  # No new schedulers added
    assert mock_session.commit.call_count == 1
    assert mock_fl_scheduler_class.call_count == 0  # No new schedulers created

    assert result == sample_all_schedulers


@patch("flip.db.seed.fl_scheduler.logger")
@patch("flip.db.seed.fl_scheduler.select")
@patch("flip.db.seed.fl_scheduler.col")
@patch("flip.db.seed.fl_scheduler.FLScheduler")
def test_seed_fl_scheduler_empty_nets_list(mock_fl_scheduler_class, mock_col, mock_select, mock_logger, mock_session):
    """Test seeding with empty nets list."""
    # Setup mocks
    mock_session.exec.return_value = MagicMock(all=MagicMock(return_value=[]))

    # Execute
    result = seed_fl_scheduler(mock_session, [])

    # Verify
    assert mock_session.exec.call_count == 1  # Only final select
    assert mock_session.add.call_count == 0  # No schedulers added
    assert mock_session.commit.call_count == 1
    assert mock_fl_scheduler_class.call_count == 0  # No schedulers created

    assert result == []


@patch("flip.db.seed.fl_scheduler.logger")
@patch("flip.db.seed.fl_scheduler.select")
@patch("flip.db.seed.fl_scheduler.col")
def test_seed_fl_scheduler_query_construction(mock_col, mock_select, mock_logger, mock_session, sample_nets):
    """Test that database queries are constructed correctly."""
    # Setup mocks
    mock_session.exec.side_effect = [
        MagicMock(first=MagicMock(return_value=None)),  # No existing for net1
        MagicMock(first=MagicMock(return_value=None)),  # No existing for net2
        MagicMock(first=MagicMock(return_value=None)),  # No existing for net3
        MagicMock(all=MagicMock(return_value=[])),  # Return empty list
    ]

    # Execute
    seed_fl_scheduler(mock_session, sample_nets)

    # Verify query construction
    assert mock_select.call_count == 4  # 3 individual checks + 1 final select
    assert mock_col.call_count == 3  # Called for each net check

    # Verify the final select call for returning all schedulers
    mock_select.assert_any_call(FLScheduler)


@patch("flip.db.seed.fl_scheduler.logger")
def test_seed_fl_scheduler_logging(mock_logger, mock_session, sample_nets):
    """Test that appropriate logging occurs."""
    # Setup mocks
    mock_session.exec.side_effect = [
        MagicMock(first=MagicMock(return_value=None)),  # No existing for net1
        MagicMock(first=MagicMock(return_value=None)),  # No existing for net2
        MagicMock(first=MagicMock(return_value=None)),  # No existing for net3
        MagicMock(all=MagicMock(return_value=[])),  # Return empty list
    ]

    with (
        patch("flip.db.seed.fl_scheduler.select"),
        patch("flip.db.seed.fl_scheduler.col"),
        patch("flip.db.seed.fl_scheduler.FLScheduler"),
    ):
        # Execute
        seed_fl_scheduler(mock_session, sample_nets)

        # Verify logging
        mock_logger.debug.assert_any_call("Seeding Federated Learning Scheduler")
        for net in sample_nets:
            mock_logger.debug.assert_any_call(f"Checking scheduler for net: {net.name} ({net.id})")
