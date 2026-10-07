# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
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
"""What the hub said about itself on the last heartbeat (FLIP#1204)."""

import hashlib
from unittest.mock import patch

import pytest

import trust_api.services.hub_status as hub_status
from trust_api.utils.encryption import aes_key_fingerprint

KEY = b"\x07" * 32
FINGERPRINT = hashlib.sha256(KEY).hexdigest()[:12]


@pytest.fixture(autouse=True)
def _reset():
    hub_status.reset()
    yield
    hub_status.reset()


@pytest.fixture
def own_key():
    with patch("trust_api.utils.encryption.get_aes_key", return_value=KEY):
        yield


def test_starts_unknown():
    assert hub_status.current() == {"hub_version": None, "hub_key_match": None, "hub_key_fingerprint": None}


@pytest.mark.usefixtures("own_key")
def test_fingerprint_is_a_short_sha256_of_the_raw_key():
    """Must match the hub's computation byte for byte — both digest the DECODED key."""
    assert aes_key_fingerprint() == FINGERPRINT


@pytest.mark.usefixtures("own_key")
def test_records_version_and_a_matching_key():
    hub_status.record({"trust_id": "x", "hub_version": "v0.7.0", "aes_key_fingerprint": FINGERPRINT})
    assert hub_status.current() == {"hub_version": "v0.7.0", "hub_key_match": True, "hub_key_fingerprint": FINGERPRINT}


@pytest.mark.usefixtures("own_key")
def test_records_a_key_mismatch_and_says_so_once(caplog):
    """A kit whose AES key was rotated under it must be visible from the trust's own /health,
    not only as every task failing with a decrypt error."""
    with caplog.at_level("WARNING"):
        hub_status.record({"hub_version": "v0.7.0", "aes_key_fingerprint": "000000000000"})
        hub_status.record({"hub_version": "v0.7.0", "aes_key_fingerprint": "000000000000"})
    assert hub_status.current()["hub_key_match"] is False
    assert sum("AES key" in r.message for r in caplog.records) == 1


@pytest.mark.usefixtures("own_key")
def test_an_older_hub_leaves_both_unknown():
    """Pre-FLIP#1204 hubs reply with the identity block only; that is not a mismatch."""
    hub_status.record({"trust_id": "x", "trust_name": "T"})
    assert hub_status.current() == {"hub_version": None, "hub_key_match": None, "hub_key_fingerprint": None}


def test_an_unreadable_own_key_reports_unknown_not_mismatch():
    with patch("trust_api.utils.encryption.get_aes_key", side_effect=ValueError("bad key")):
        hub_status.record({"hub_version": "v0.7.0", "aes_key_fingerprint": FINGERPRINT})
    assert hub_status.current() == {"hub_version": "v0.7.0", "hub_key_match": None, "hub_key_fingerprint": FINGERPRINT}


@pytest.mark.usefixtures("own_key")
def test_a_failed_heartbeat_forgets_what_the_hub_said():
    """A match recorded before the hub rotated its key must not outlive the heartbeats it rejects."""
    hub_status.record({"hub_version": "v0.7.0", "aes_key_fingerprint": FINGERPRINT})
    hub_status.forget()
    assert hub_status.current() == {"hub_version": None, "hub_key_match": None, "hub_key_fingerprint": None}


@pytest.mark.usefixtures("own_key")
def test_the_mismatch_warning_survives_a_forget_but_re_arms_after_a_match(caplog):
    with caplog.at_level("WARNING"):
        hub_status.record({"aes_key_fingerprint": "000000000000"})
        hub_status.forget()
        hub_status.record({"aes_key_fingerprint": "000000000000"})
        hub_status.record({"aes_key_fingerprint": FINGERPRINT})
        hub_status.record({"aes_key_fingerprint": "000000000000"})
    assert sum("AES key" in r.message for r in caplog.records) == 2
