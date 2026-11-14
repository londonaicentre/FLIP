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

from flip_api.db.seed import seed_essential_data

SEEDERS = [
    "seed_roles",
    "seed_permissions",
    "seed_role_permissions",
    "seed_main_users",
    "seed_fl_kit_slots",
    "seed_banner",
    "seed_config",
    "seed_fl_nets",
    "seed_fl_scheduler",
]


@pytest.fixture
def seeders():
    """Patch every seeder, the engine and the session; yield (parent mock, session)."""
    parent = MagicMock()
    session = MagicMock()
    with (
        patch.multiple(seed_essential_data, **{name: getattr(parent, name) for name in SEEDERS}),
        patch.object(seed_essential_data, "get_engine"),
        patch.object(seed_essential_data, "Session") as session_cls,
    ):
        session_cls.return_value.__enter__.return_value = session
        yield parent, session


def test_main_runs_every_seeder_in_order_then_commits(seeders):
    parent, session = seeders

    seed_essential_data.main()

    # Roles and permissions precede their join table; nets precede the scheduler built on them.
    assert [c[0] for c in parent.mock_calls] == SEEDERS
    parent.seed_fl_scheduler.assert_called_once_with(session, parent.seed_fl_nets.return_value)
    session.commit.assert_called_once()
    session.rollback.assert_not_called()


def test_main_rolls_back_and_reraises_when_a_seeder_fails(seeders):
    """The entrypoint relies on the non-zero exit to fail the boot."""
    parent, session = seeders
    parent.seed_banner.side_effect = RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        seed_essential_data.main()

    session.rollback.assert_called_once()
    session.commit.assert_not_called()
    parent.seed_config.assert_not_called()
