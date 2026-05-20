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
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlmodel import Session

from flip_api.db.models.main_models import FLKitSlot, Trust
from flip_api.db.seed.fl_kit_slots import _slot_number, seed_fl_kit_slots


@pytest.fixture
def mock_session():
    return MagicMock(spec=Session)


class TestSlotNumberDerivation:
    def test_trailing_integer_extracted(self):
        assert _slot_number("Trust_007") == 7
        assert _slot_number("Trust_1") == 1
        assert _slot_number("Site_42") == 42

    def test_no_trailing_integer_falls_back_to_zero(self):
        assert _slot_number("GSTT") == 0


@patch("flip_api.db.seed.fl_kit_slots.get_settings")
def test_seed_inserts_new_slots(mock_get_settings, mock_session):
    mock_get_settings.return_value = SimpleNamespace(
        FL_KIT_SLOT_NAMES=["Trust_1", "Trust_2"], TRUST_DISPLAY_NAMES={}
    )
    # Phase 1: both slot lookups return None (new); phase 2: backfill query returns []
    # (no slots to assign).
    mock_session.exec.side_effect = [
        MagicMock(first=MagicMock(return_value=None)),
        MagicMock(first=MagicMock(return_value=None)),
        MagicMock(all=MagicMock(return_value=[])),
    ]

    seed_fl_kit_slots(mock_session)

    added_slots = [c.args[0] for c in mock_session.add.call_args_list]
    assert all(isinstance(s, FLKitSlot) for s in added_slots)
    by_name = {s.slot_name: s for s in added_slots}
    assert by_name["Trust_1"].slot_number == 1
    assert by_name["Trust_2"].slot_number == 2
    # Two commits: one for the inserts, one for the backfill pass (even if no-op).
    assert mock_session.commit.call_count == 2


@patch("flip_api.db.seed.fl_kit_slots.get_settings")
def test_seed_skips_existing_slots(mock_get_settings, mock_session):
    mock_get_settings.return_value = SimpleNamespace(
        FL_KIT_SLOT_NAMES=["Trust_1"], TRUST_DISPLAY_NAMES={}
    )
    existing = FLKitSlot(slot_name="Trust_1", slot_number=1)
    mock_session.exec.side_effect = [
        MagicMock(first=MagicMock(return_value=existing)),
        MagicMock(all=MagicMock(return_value=[])),
    ]

    seed_fl_kit_slots(mock_session)

    mock_session.add.assert_not_called()


@patch("flip_api.db.seed.fl_kit_slots.get_settings")
def test_seed_backfills_slot_to_matching_named_trust(mock_get_settings, mock_session):
    """A seeded trust whose name matches a slot_name gets that slot bound to it."""
    mock_get_settings.return_value = SimpleNamespace(
        FL_KIT_SLOT_NAMES=["Trust_1"], TRUST_DISPLAY_NAMES={}
    )
    existing_slot = FLKitSlot(slot_name="Trust_1", slot_number=1)
    matching_trust = Trust(id=uuid4(), name="Trust_1")

    mock_session.exec.side_effect = [
        MagicMock(first=MagicMock(return_value=existing_slot)),  # slot already exists
        MagicMock(all=MagicMock(return_value=[existing_slot])),  # unassigned-slots query
        MagicMock(first=MagicMock(return_value=matching_trust)),  # trust-by-name lookup
    ]

    seed_fl_kit_slots(mock_session)

    assert existing_slot.assigned_to_trust_id == matching_trust.id


@patch("flip_api.db.seed.fl_kit_slots.get_settings")
def test_seed_backfills_slot_to_display_named_trust(mock_get_settings, mock_session):
    """A slot resolves through TRUST_DISPLAY_NAMES to the trust's persisted display name."""
    mock_get_settings.return_value = SimpleNamespace(
        FL_KIT_SLOT_NAMES=["Trust_1"],
        TRUST_DISPLAY_NAMES={"Trust_1": "Open Trust (EC2)"},
    )
    existing_slot = FLKitSlot(slot_name="Trust_1", slot_number=1)
    matching_trust = Trust(id=uuid4(), name="Open Trust (EC2)")

    mock_session.exec.side_effect = [
        MagicMock(first=MagicMock(return_value=existing_slot)),  # slot already exists
        MagicMock(all=MagicMock(return_value=[existing_slot])),  # unassigned-slots query
        MagicMock(first=MagicMock(return_value=matching_trust)),  # trust-by-display-name lookup
    ]

    seed_fl_kit_slots(mock_session)

    assert existing_slot.assigned_to_trust_id == matching_trust.id


@patch("flip_api.db.seed.fl_kit_slots.get_settings")
def test_seed_empty_pool_is_noop(mock_get_settings, mock_session):
    mock_get_settings.return_value = SimpleNamespace(FL_KIT_SLOT_NAMES=[], TRUST_DISPLAY_NAMES={})

    seed_fl_kit_slots(mock_session)

    mock_session.add.assert_not_called()
    # Only the insert-pass commit fires; the backfill pass is skipped when the pool is empty.
    mock_session.commit.assert_called_once()
