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

import pytest
from botocore.exceptions import ClientError
from sqlmodel import Session

from flip_api.db.models.main_models import FLKitSlot
from flip_api.db.seed.fl_kit_slots import (
    _NON_NUMERIC_SLOT_NUMBER,
    _slot_number,
    resolve_fl_kit_slot_names,
    seed_fl_kit_slots,
)


@pytest.fixture
def mock_session():
    return MagicMock(spec=Session)


class TestSlotNumberDerivation:
    def test_trailing_integer_extracted(self):
        assert _slot_number("Trust_007") == 7
        assert _slot_number("Trust_1") == 1
        assert _slot_number("Site_42") == 42

    def test_no_trailing_integer_returns_sentinel(self):
        assert _slot_number("GSTT") == _NON_NUMERIC_SLOT_NUMBER
        assert _slot_number("Trust_K8s") == _NON_NUMERIC_SLOT_NUMBER
        assert _NON_NUMERIC_SLOT_NUMBER > 0


@patch("flip_api.db.seed.fl_kit_slots.get_settings")
def test_seed_inserts_new_slots(mock_get_settings, mock_session):
    """Each missing slot name from FL_KIT_SLOT_NAMES is inserted."""
    mock_get_settings.return_value = SimpleNamespace(ENV="development", FL_KIT_SLOT_NAMES=["Trust_1", "Trust_2"])
    mock_session.exec.side_effect = [
        MagicMock(first=MagicMock(return_value=None)),
        MagicMock(first=MagicMock(return_value=None)),
    ]

    seed_fl_kit_slots(mock_session)

    added_slots = [c.args[0] for c in mock_session.add.call_args_list]
    assert all(isinstance(s, FLKitSlot) for s in added_slots)
    by_name = {s.slot_name: s for s in added_slots}
    assert by_name["Trust_1"].slot_number == 1
    assert by_name["Trust_2"].slot_number == 2
    mock_session.commit.assert_called_once()


@patch("flip_api.db.seed.fl_kit_slots.get_settings")
def test_seed_skips_existing_slots(mock_get_settings, mock_session):
    """A pre-existing slot row is left untouched."""
    mock_get_settings.return_value = SimpleNamespace(ENV="development", FL_KIT_SLOT_NAMES=["Trust_1"])
    existing = FLKitSlot(slot_name="Trust_1", slot_number=1)
    mock_session.exec.side_effect = [MagicMock(first=MagicMock(return_value=existing))]

    seed_fl_kit_slots(mock_session)

    mock_session.add.assert_not_called()


@patch("flip_api.db.seed.fl_kit_slots.get_settings")
def test_seed_empty_pool_is_noop(mock_get_settings, mock_session):
    """No slots configured → nothing inserted, no slot→trust backfill (register_trust handles it)."""
    mock_get_settings.return_value = SimpleNamespace(ENV="development", FL_KIT_SLOT_NAMES=[])

    seed_fl_kit_slots(mock_session)

    mock_session.add.assert_not_called()
    mock_session.commit.assert_called_once()


class TestResolveFlKitSlotNames:
    """The env-appropriate source split: dev = env var, prod = FLIP_API secret (env fallback)."""

    @patch("flip_api.db.seed.fl_kit_slots.get_secret")
    @patch("flip_api.db.seed.fl_kit_slots.get_settings")
    def test_dev_reads_settings_and_never_touches_the_secret(self, mock_get_settings, mock_get_secret):
        mock_get_settings.return_value = SimpleNamespace(ENV="development", FL_KIT_SLOT_NAMES=["Trust_1"])

        assert resolve_fl_kit_slot_names() == ["Trust_1"]
        mock_get_secret.assert_not_called()

    @patch("flip_api.db.seed.fl_kit_slots.get_secret")
    @patch("flip_api.db.seed.fl_kit_slots.get_settings")
    def test_prod_parses_json_list_from_secret(self, mock_get_settings, mock_get_secret):
        mock_get_settings.return_value = SimpleNamespace(ENV="production", FL_KIT_SLOT_NAMES=["Trust_1"])
        mock_get_secret.return_value = '["Trust_1", "Trust_2", "Trust_3"]'

        assert resolve_fl_kit_slot_names() == ["Trust_1", "Trust_2", "Trust_3"]
        mock_get_secret.assert_called_once_with("fl_kit_slot_names")

    @patch("flip_api.db.seed.fl_kit_slots.get_secret")
    @patch("flip_api.db.seed.fl_kit_slots.get_settings")
    def test_prod_missing_secret_key_falls_back_to_settings(self, mock_get_settings, mock_get_secret):
        mock_get_settings.return_value = SimpleNamespace(ENV="production", FL_KIT_SLOT_NAMES=["Trust_1"])
        mock_get_secret.side_effect = KeyError("fl_kit_slot_names")

        assert resolve_fl_kit_slot_names() == ["Trust_1"]

    @patch("flip_api.db.seed.fl_kit_slots.get_secret")
    @patch("flip_api.db.seed.fl_kit_slots.get_settings")
    def test_prod_aws_error_falls_back_to_settings(self, mock_get_settings, mock_get_secret):
        mock_get_settings.return_value = SimpleNamespace(ENV="production", FL_KIT_SLOT_NAMES=["Trust_1"])
        mock_get_secret.side_effect = ClientError({"Error": {"Code": "AccessDenied"}}, "GetSecretValue")

        assert resolve_fl_kit_slot_names() == ["Trust_1"]

    @pytest.mark.parametrize("payload", ["not json", '"Trust_1"', '{"Trust_1": 1}', '["Trust_1", 2]'])
    @patch("flip_api.db.seed.fl_kit_slots.get_secret")
    @patch("flip_api.db.seed.fl_kit_slots.get_settings")
    def test_prod_malformed_secret_value_falls_back_to_settings(self, mock_get_settings, mock_get_secret, payload):
        mock_get_settings.return_value = SimpleNamespace(ENV="production", FL_KIT_SLOT_NAMES=["Trust_1"])
        mock_get_secret.return_value = payload

        assert resolve_fl_kit_slot_names() == ["Trust_1"]
