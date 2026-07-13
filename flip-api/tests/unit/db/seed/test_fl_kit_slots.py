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

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from sqlmodel import Session

from flip_api.db.models.main_models import FLKitSlot
from flip_api.db.seed.fl_kit_slots import (
    _NON_NUMERIC_SLOT_NUMBER,
    _slot_number,
    insert_missing_slots,
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
    """Single source per env: dev = the DevSettings-only env var, prod = the FLIP_API
    secret with NO env fallback (a broken secret yields an empty list, loudly).
    """

    @patch("flip_api.db.seed.fl_kit_slots.get_secret")
    @patch("flip_api.db.seed.fl_kit_slots.get_settings")
    def test_dev_reads_settings_and_never_touches_the_secret(self, mock_get_settings, mock_get_secret):
        mock_get_settings.return_value = SimpleNamespace(ENV="development", FL_KIT_SLOT_NAMES=["Trust_1"])

        assert resolve_fl_kit_slot_names() == ["Trust_1"]
        mock_get_secret.assert_not_called()

    @patch("flip_api.db.seed.fl_kit_slots.get_secret")
    @patch("flip_api.db.seed.fl_kit_slots.get_settings")
    def test_prod_parses_json_list_from_secret(self, mock_get_settings, mock_get_secret):
        mock_get_settings.return_value = SimpleNamespace(ENV="production")
        mock_get_secret.return_value = '["Trust_1", "Trust_2", "Trust_3"]'

        assert resolve_fl_kit_slot_names() == ["Trust_1", "Trust_2", "Trust_3"]
        mock_get_secret.assert_called_once_with("fl_kit_slot_names")

    @patch("flip_api.db.seed.fl_kit_slots.get_secret")
    @patch("flip_api.db.seed.fl_kit_slots.get_settings")
    def test_prod_missing_key_returns_empty_and_warns(self, mock_get_settings, mock_get_secret, caplog):
        """The missing key is the expected state until the first apply-flip-secret after
        deploy — WARNING (not ERROR), empty list, and no env-var resurrection.
        """
        mock_get_settings.return_value = SimpleNamespace(ENV="production")
        mock_get_secret.side_effect = KeyError("fl_kit_slot_names")

        with caplog.at_level(logging.WARNING, logger="flip_api.seed"):
            assert resolve_fl_kit_slot_names() == []

        assert "fl_kit_slot_names" in caplog.text
        assert caplog.records[-1].levelno == logging.WARNING

    @patch("flip_api.db.seed.fl_kit_slots.get_secret")
    @patch("flip_api.db.seed.fl_kit_slots.get_settings")
    def test_prod_aws_error_returns_empty(self, mock_get_settings, mock_get_secret):
        mock_get_settings.return_value = SimpleNamespace(ENV="production")
        mock_get_secret.side_effect = ClientError({"Error": {"Code": "AccessDenied"}}, "GetSecretValue")

        assert resolve_fl_kit_slot_names() == []

    @patch("flip_api.db.seed.fl_kit_slots.get_secret")
    @patch("flip_api.db.seed.fl_kit_slots.get_settings")
    def test_prod_botocore_error_returns_empty(self, mock_get_settings, mock_get_secret):
        """BotoCoreError subclasses (no credentials, endpoint unreachable) aren't ClientError."""
        mock_get_settings.return_value = SimpleNamespace(ENV="production")
        mock_get_secret.side_effect = EndpointConnectionError(endpoint_url="https://secretsmanager.test")

        assert resolve_fl_kit_slot_names() == []

    # The last two payloads are NOT strings: a hand-edited secret holding a native JSON
    # array (or null) makes json.loads raise TypeError, which must return empty rather
    # than crash-loop the fail-fast boot seed.
    @pytest.mark.parametrize(
        "payload", ["not json", '"Trust_1"', '{"Trust_1": 1}', '["Trust_1", 2]', ["Trust_1"], None]
    )
    @patch("flip_api.db.seed.fl_kit_slots.get_secret")
    @patch("flip_api.db.seed.fl_kit_slots.get_settings")
    def test_prod_malformed_secret_value_returns_empty_and_logs_error(
        self, mock_get_settings, mock_get_secret, payload, caplog
    ):
        mock_get_settings.return_value = SimpleNamespace(ENV="production")
        mock_get_secret.return_value = payload

        with caplog.at_level(logging.ERROR, logger="flip_api.seed"):
            assert resolve_fl_kit_slot_names() == []

        # A malformed value is broken config (not the transient rollout state), so the
        # empty result must be loud: ERROR, naming the secret key.
        assert "fl_kit_slot_names" in caplog.text
        assert caplog.records[-1].levelno == logging.ERROR

    @patch("flip_api.db.seed.fl_kit_slots.get_secret")
    @patch("flip_api.db.seed.fl_kit_slots.get_settings")
    def test_prod_empty_secret_list_is_respected(self, mock_get_settings, mock_get_secret):
        """A valid empty list is configuration, not an error — no log noise, no growth."""
        mock_get_settings.return_value = SimpleNamespace(ENV="production")
        mock_get_secret.return_value = "[]"

        assert resolve_fl_kit_slot_names() == []


@patch("flip_api.db.seed.fl_kit_slots.get_secret")
@patch("flip_api.db.seed.fl_kit_slots.get_settings")
def test_seed_reads_the_secret_in_prod(mock_get_settings, mock_get_secret, mock_session):
    """Boot seeding goes through the resolver: in prod the secret feeds the pool."""
    mock_get_settings.return_value = SimpleNamespace(ENV="production", FL_KIT_SLOT_NAMES=[])
    mock_get_secret.return_value = '["Trust_9"]'
    mock_session.exec.side_effect = [MagicMock(first=MagicMock(return_value=None))]

    seed_fl_kit_slots(mock_session)

    assert [c.args[0].slot_name for c in mock_session.add.call_args_list] == ["Trust_9"]
    mock_session.commit.assert_called_once()


def test_insert_missing_slots_returns_only_new_names_and_never_commits(mock_session):
    """The return value feeds the reconcile's log line, and committing is the caller's
    job — a commit here would break register_trust's savepoint semantics.
    """
    existing = FLKitSlot(slot_name="Trust_1", slot_number=1)
    mock_session.exec.side_effect = [
        MagicMock(first=MagicMock(return_value=existing)),
        MagicMock(first=MagicMock(return_value=None)),
    ]

    inserted = insert_missing_slots(mock_session, ["Trust_1", "Trust_2"])

    assert inserted == ["Trust_2"]
    mock_session.commit.assert_not_called()
