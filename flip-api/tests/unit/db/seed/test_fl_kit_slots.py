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
from sqlmodel import Session

from flip_api.db.models.main_models import FLKitSlot
from flip_api.db.seed.fl_kit_slots import (
    _NON_NUMERIC_SLOT_NUMBER,
    FLARE_KIT_REQUIRED_FILES,
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
    mock_session.exec.return_value.all.return_value = []

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
    mock_session.exec.return_value.all.return_value = ["Trust_1"]

    seed_fl_kit_slots(mock_session)

    mock_session.add.assert_not_called()


@patch("flip_api.db.seed.fl_kit_slots.get_settings")
def test_seed_empty_pool_is_noop(mock_get_settings, mock_session):
    """No slots configured → nothing inserted, no slot→trust backfill (register_trust handles it)."""
    mock_get_settings.return_value = SimpleNamespace(ENV="development", FL_KIT_SLOT_NAMES=[])

    seed_fl_kit_slots(mock_session)

    mock_session.add.assert_not_called()
    mock_session.commit.assert_called_once()


@patch("flip_api.db.seed.fl_kit_slots.S3Client")
@patch("flip_api.db.seed.fl_kit_slots.get_settings")
def test_dev_reads_settings_and_never_touches_s3(mock_get_settings, mock_s3):
    """Single source per env: dev = the DevSettings-only env var; prod reads S3 (the classes below)."""
    mock_get_settings.return_value = SimpleNamespace(ENV="development", FL_KIT_SLOT_NAMES=["Trust_1"])

    assert resolve_fl_kit_slot_names() == ["Trust_1"]
    mock_s3.assert_not_called()


_KITS = "s3://kits-bucket/fl-flare-participant-kits/20260704"


def _kit(net: str, slot: str, files=FLARE_KIT_REQUIRED_FILES) -> list[str]:
    return [f"{_KITS}/{net}/services/{slot}/startup/{name}" for name in files]


# The hub's own kits, as provisioned: never claimable.
_SERVER_AND_ADMIN_KITS = [
    *_kit("net-1", "fl-server-net-1", ["fed_server.json", "rootCA.pem", "server.crt", "server.key", "sub_start.sh"]),
    *_kit("net-1", "flip-fl-api-net-1", ["client.crt", "client.key", "fed_admin.json", "rootCA.pem"]),
]


@patch("flip_api.db.seed.fl_kit_slots.S3Client")
@patch("flip_api.db.seed.fl_kit_slots.get_settings")
class TestCompleteFlareKitNames:
    """Prod NVFLARE: the pool is every slot whose kit is complete on every net."""

    def _settings(self, mock_get_settings, uri=_KITS):
        mock_get_settings.return_value = SimpleNamespace(ENV="production", FL_BACKEND="nvflare", FLARE_KITS_S3_URI=uri)

    def test_kit_complete_on_every_net_is_claimable(self, mock_get_settings, mock_s3, caplog):
        self._settings(mock_get_settings)
        mock_s3.return_value.list_objects.return_value = [
            *_SERVER_AND_ADMIN_KITS,
            *_kit("net-1", "Trust_10"),
            *_kit("net-2", "Trust_10"),
            *_kit("net-1", "Trust_2"),
            *_kit("net-2", "Trust_2"),
            f"{_KITS}/net-2/state/cert.json",
        ]

        # Numeric order, server/admin kits left out.
        assert resolve_fl_kit_slot_names() == ["Trust_2", "Trust_10"]
        mock_s3.return_value.list_objects.assert_called_once_with(f"{_KITS}/")
        # The hub's own kits are expected to fall short: no warning for them.
        assert not caplog.records

    def test_kit_incomplete_or_missing_on_a_net_is_not_claimable(self, mock_get_settings, mock_s3, caplog):
        """An interrupted upload, or a net added after the kit was minted: left out, and the log says why."""
        self._settings(mock_get_settings)
        mock_s3.return_value.list_objects.return_value = [
            *_kit("net-1", "Trust_1"),
            *_kit("net-2", "Trust_1"),
            *_kit("net-1", "Trust_2"),
            *_kit("net-2", "Trust_2", FLARE_KIT_REQUIRED_FILES - {"client.key"}),
            *_kit("net-1", "Trust_3"),
            *_kit("net-2", "fl-server-net-2", ["fed_server.json", "rootCA.pem"]),
        ]

        with caplog.at_level(logging.WARNING, logger="flip_api.seed"):
            assert resolve_fl_kit_slot_names() == ["Trust_1"]

        assert [record.getMessage() for record in caplog.records] == [
            "FLARE kit 'Trust_2' is not claimable: net-2 lacks client.key.",
            "FLARE kit 'Trust_3' is not claimable: net-2 lacks "
            "client.crt, client.key, fed_client.json, rootCA.pem, sub_start.sh.",
        ]

    def test_upload_interrupted_before_the_required_files_is_named_on_a_single_net(
        self, mock_get_settings, mock_s3, caplog
    ):
        """The required files go up last, so an interrupted kit has none of them: still warned about."""
        self._settings(mock_get_settings)
        mock_s3.return_value.list_objects.return_value = [
            *_SERVER_AND_ADMIN_KITS,
            *_kit("net-1", "Trust_1"),
            *_kit("net-1", "Trust_2", ["start.sh", "stop_fl.sh"]),
            f"{_KITS}/net-1/services/Trust_3/local/resources.json.default",
        ]

        with caplog.at_level(logging.WARNING, logger="flip_api.seed"):
            assert resolve_fl_kit_slot_names() == ["Trust_1"]

        lacks = "net-1 lacks client.crt, client.key, fed_client.json, rootCA.pem, sub_start.sh."
        assert [record.getMessage() for record in caplog.records] == [
            f"FLARE kit 'Trust_2' is not claimable: {lacks}",
            f"FLARE kit 'Trust_3' is not claimable: {lacks}",
        ]

    def test_listing_failure_is_unreadable_and_logs_error(self, mock_get_settings, mock_s3, caplog):
        self._settings(mock_get_settings)
        mock_s3.return_value.list_objects.side_effect = Exception("AccessDenied")

        with caplog.at_level(logging.ERROR, logger="flip_api.seed"):
            assert resolve_fl_kit_slot_names() is None

        assert "AccessDenied" in caplog.text
        assert caplog.records[-1].levelno == logging.ERROR

    def test_unset_uri_returns_empty_without_listing(self, mock_get_settings, mock_s3, caplog):
        self._settings(mock_get_settings, uri="")

        with caplog.at_level(logging.ERROR, logger="flip_api.seed"):
            assert resolve_fl_kit_slot_names() == []

        mock_s3.return_value.list_objects.assert_not_called()
        assert caplog.records[-1].levelno == logging.ERROR

    def test_no_kits_returns_empty_and_logs_error(self, mock_get_settings, mock_s3, caplog):
        self._settings(mock_get_settings)
        mock_s3.return_value.list_objects.return_value = []

        with caplog.at_level(logging.ERROR, logger="flip_api.seed"):
            assert resolve_fl_kit_slot_names() == []

        assert caplog.records[-1].levelno == logging.ERROR


_KEYS = "s3://kits-bucket/fl-flower-participant-kits/20260811"
# The hub's side of a Flower kit, as provisioned: names no slot.
_CERTIFICATES = [f"{_KEYS}/net-1/certificates/{name}" for name in ("ca.crt", "server.pem", "server.key")]


def _key_pair(net: str, number: int, halves=("", ".pub")) -> list[str]:
    return [f"{_KEYS}/{net}/keys/supernode_credentials_{number}{half}" for half in halves]


@patch("flip_api.db.seed.fl_kit_slots.S3Client")
@patch("flip_api.db.seed.fl_kit_slots.get_settings")
class TestCompleteFlowerKeyNames:
    """Prod Flower: the pool is every slot whose SuperNode key pair is complete on every net."""

    def _settings(self, mock_get_settings, uri=_KEYS):
        mock_get_settings.return_value = SimpleNamespace(ENV="production", FL_BACKEND="flower", FLOWER_KITS_S3_URI=uri)

    def test_key_pair_complete_on_every_net_is_claimable(self, mock_get_settings, mock_s3, caplog):
        self._settings(mock_get_settings)
        mock_s3.return_value.list_objects.return_value = [
            *_CERTIFICATES,
            *_key_pair("net-1", 10),
            *_key_pair("net-2", 10),
            *_key_pair("net-1", 2),
            *_key_pair("net-2", 2),
            f"{_KEYS}/net-2/certificates/ca.crt",
        ]

        # Numeric order, each slot named after its key's number.
        assert resolve_fl_kit_slot_names() == ["Trust_2", "Trust_10"]
        mock_s3.return_value.list_objects.assert_called_once_with(f"{_KEYS}/")
        assert not caplog.records

    def test_key_not_yet_registered_with_the_superlink_is_not_claimable(self, mock_get_settings, mock_s3, caplog):
        """add_fl_kits.sh uploads the private key only after the registration: until then, left out."""
        self._settings(mock_get_settings)
        mock_s3.return_value.list_objects.return_value = [
            *_CERTIFICATES,
            *_key_pair("net-1", 1),
            *_key_pair("net-1", 2, halves=(".pub",)),
        ]

        with caplog.at_level(logging.WARNING, logger="flip_api.seed"):
            assert resolve_fl_kit_slot_names() == ["Trust_1"]

        assert [record.getMessage() for record in caplog.records] == [
            "Flower key 'Trust_2' is not claimable: net-1 lacks supernode_credentials_2.",
        ]

    def test_key_pair_incomplete_or_missing_on_a_net_is_not_claimable(self, mock_get_settings, mock_s3, caplog):
        """A withdrawn half, or a net added after the key was minted: left out, and the log says why."""
        self._settings(mock_get_settings)
        mock_s3.return_value.list_objects.return_value = [
            *_key_pair("net-1", 1),
            *_key_pair("net-2", 1),
            *_key_pair("net-1", 2),
            *_key_pair("net-2", 2, halves=("",)),
            *_key_pair("net-1", 3),
            f"{_KEYS}/net-2/keys/notes.txt",
        ]

        with caplog.at_level(logging.WARNING, logger="flip_api.seed"):
            assert resolve_fl_kit_slot_names() == ["Trust_1"]

        assert [record.getMessage() for record in caplog.records] == [
            "Flower key 'Trust_2' is not claimable: net-2 lacks supernode_credentials_2.pub.",
            "Flower key 'Trust_3' is not claimable: net-2 lacks supernode_credentials_3, supernode_credentials_3.pub.",
        ]

    def test_listing_failure_is_unreadable_and_logs_error(self, mock_get_settings, mock_s3, caplog):
        self._settings(mock_get_settings)
        mock_s3.return_value.list_objects.side_effect = Exception("AccessDenied")

        with caplog.at_level(logging.ERROR, logger="flip_api.seed"):
            assert resolve_fl_kit_slot_names() is None

        assert "AccessDenied" in caplog.text
        assert caplog.records[-1].levelno == logging.ERROR

    def test_unset_uri_returns_empty_without_listing(self, mock_get_settings, mock_s3, caplog):
        self._settings(mock_get_settings, uri="")

        with caplog.at_level(logging.ERROR, logger="flip_api.seed"):
            assert resolve_fl_kit_slot_names() == []

        mock_s3.return_value.list_objects.assert_not_called()
        assert caplog.records[-1].levelno == logging.ERROR

    def test_no_keys_returns_empty_and_logs_error(self, mock_get_settings, mock_s3, caplog):
        self._settings(mock_get_settings)
        mock_s3.return_value.list_objects.return_value = []

        with caplog.at_level(logging.ERROR, logger="flip_api.seed"):
            assert resolve_fl_kit_slot_names() == []

        assert caplog.records[-1].levelno == logging.ERROR

    def test_seed_reads_the_keys(self, mock_get_settings, mock_s3, mock_session):
        """Boot seeding goes through the resolver: in prod the keys in S3 feed the pool."""
        self._settings(mock_get_settings)
        mock_s3.return_value.list_objects.return_value = _key_pair("net-1", 9)
        mock_session.exec.return_value.all.return_value = []

        seed_fl_kit_slots(mock_session)

        assert [c.args[0].slot_name for c in mock_session.add.call_args_list] == ["Trust_9"]
        mock_session.commit.assert_called_once()


def test_insert_missing_slots_returns_only_new_names_and_never_commits(mock_session):
    """The return value feeds the reconcile's log line, and committing is the caller's
    job — a commit here would break register_trust's savepoint semantics.
    """
    mock_session.exec.return_value.all.return_value = ["Trust_1"]

    inserted = insert_missing_slots(mock_session, ["Trust_1", "Trust_2"])

    assert inserted == ["Trust_2"]
    mock_session.exec.assert_called_once()  # one query for all the names
    mock_session.commit.assert_not_called()
