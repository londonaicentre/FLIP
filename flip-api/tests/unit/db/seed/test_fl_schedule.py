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
from flip_api.db.seed.fl_scheduler import seed_fl_scheduler
from flip_api.domain.schemas.status import NetStatus


@pytest.fixture
def mock_session():
    """Create a mock session for testing."""
    session = MagicMock(spec=Session)
    return session


def _net(i):
    net = MagicMock(spec=FLNets, id=i)
    net.name = f"test_net_{i}"  # set after construction: the `name` kwarg would name the mock itself
    return net


def _scheduler(i):
    return MagicMock(spec=FLScheduler, id=i, net_id=i, status=NetStatus.AVAILABLE)


@pytest.fixture
def sample_nets():
    """Create sample FLNets for testing."""
    return [_net(i) for i in (1, 2, 3)]


@pytest.fixture
def sample_existing_scheduler():
    """Create a sample existing scheduler."""
    return _scheduler(1)


@pytest.fixture
def sample_all_schedulers():
    """Create sample schedulers that would be returned."""
    return [_scheduler(i) for i in (1, 2, 3)]


@pytest.fixture
def scheduler_cls():
    """Patch the query builders and the logger; yield the FLScheduler class double."""
    mod = "flip_api.db.seed.fl_scheduler"
    with patch(f"{mod}.logger"), patch(f"{mod}.select"), patch(f"{mod}.col"), patch(f"{mod}.FLScheduler") as cls:
        yield cls


@pytest.mark.parametrize(
    "existing",
    [[False, False, False], [True, False, False], [True, True, True], []],
    ids=["none-existing", "some-existing", "all-existing", "no-nets"],
)
def test_seed_fl_scheduler_creates_only_missing_schedulers(
    existing, scheduler_cls, mock_session, sample_nets, sample_existing_scheduler, sample_all_schedulers
):
    """A scheduler is created for each net without one; existing ones are kept; one commit; all are returned."""
    nets = sample_nets[: len(existing)]
    missing = [net for net, exists in zip(nets, existing, strict=True) if not exists]
    new_schedulers = [MagicMock() for _ in missing]
    scheduler_cls.side_effect = new_schedulers
    # One existence check per net, then the final select of every scheduler.
    mock_session.exec.side_effect = [
        MagicMock(first=MagicMock(return_value=sample_existing_scheduler if exists else None)) for exists in existing
    ] + [MagicMock(all=MagicMock(return_value=sample_all_schedulers))]

    result = seed_fl_scheduler(mock_session, nets)

    assert mock_session.exec.call_count == len(nets) + 1
    assert mock_session.add.call_count == len(missing)
    assert scheduler_cls.call_count == len(missing)
    assert mock_session.commit.call_count == 1
    for net, scheduler in zip(missing, new_schedulers, strict=True):
        scheduler_cls.assert_any_call(status=NetStatus.AVAILABLE, net_id=net.id)
        mock_session.add.assert_any_call(scheduler)
    assert result == sample_all_schedulers


@patch("flip_api.db.seed.fl_scheduler.logger")
@patch("flip_api.db.seed.fl_scheduler.select")
@patch("flip_api.db.seed.fl_scheduler.col")
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


@patch("flip_api.db.seed.fl_scheduler.logger")
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
        patch("flip_api.db.seed.fl_scheduler.select"),
        patch("flip_api.db.seed.fl_scheduler.col"),
        patch("flip_api.db.seed.fl_scheduler.FLScheduler"),
    ):
        # Execute
        seed_fl_scheduler(mock_session, sample_nets)

        # Verify logging
        mock_logger.debug.assert_any_call("Seeding Federated Learning Scheduler")
        for net in sample_nets:
            mock_logger.debug.assert_any_call(f"Checking scheduler for net: {net.name} ({net.id})")
