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

"""Tests for the startup audit of per-trust payload-encryption keys."""

import base64
import json
import logging
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from flip_api.trusts_services.services.trust_key_config import (
    audit_trust_key_config,
    kid_for_trust_id,
    log_trust_key_config,
)
from flip_api.utils import encryption

SHARED = base64.b64encode(b"0" * 32).decode()


def _trust(name: str, code: str | None = None):
    trust = MagicMock()
    trust.id = uuid4()
    trust.name = name
    trust.code = code
    return trust


def _session(trusts):
    session = MagicMock()
    session.exec.return_value.all.return_value = trusts
    return session


@pytest.fixture(autouse=True)
def _clean_keyring(monkeypatch):
    """Reset the memoised keyring around each test, and pin a known shared key."""
    monkeypatch.setattr(encryption, "_shared_key", lambda: base64.b64decode(SHARED))
    monkeypatch.delenv("AES_TRUST_KEYS", raising=False)
    monkeypatch.delenv("TRUST_AES_KID", raising=False)
    monkeypatch.delenv("TRUST_AES_KEY_BASE64", raising=False)
    encryption._reset_caches()
    yield
    encryption._reset_caches()


def _set_trust_keys(monkeypatch, mapping: dict[str, str]) -> None:
    monkeypatch.setenv("AES_TRUST_KEYS", json.dumps(mapping))
    encryption._reset_caches()


def test_no_per_trust_keys_puts_every_trust_on_the_shared_key():
    trusts = [_trust("GSTT"), _trust("KCH")]
    report = audit_trust_key_config(_session(trusts))

    assert report.covered == []
    assert sorted(report.on_shared_key) == ["GSTT", "KCH"]
    assert report.orphan_kids == []
    assert report.total_trusts == 2


def test_trust_with_a_key_is_reported_as_covered(monkeypatch):
    gstt, kch = _trust("GSTT"), _trust("KCH")
    _set_trust_keys(monkeypatch, {kid_for_trust_id(gstt.id): base64.b64encode(b"1" * 32).decode()})

    report = audit_trust_key_config(_session([gstt, kch]))

    assert report.covered == ["GSTT"]
    assert report.on_shared_key == ["KCH"]
    assert report.orphan_kids == []


def test_kid_by_trust_code_also_counts_as_covered(monkeypatch):
    """``kid_for_trust`` falls back to the code, so the audit must match on it too."""
    gstt = _trust("GSTT", code="GSTT")
    _set_trust_keys(monkeypatch, {"trust-GSTT": base64.b64encode(b"1" * 32).decode()})

    report = audit_trust_key_config(_session([gstt]))

    assert report.covered == ["GSTT"]
    assert report.orphan_kids == []


def test_kid_matching_no_trust_is_reported_as_an_orphan(monkeypatch):
    """The typo case: the key is loaded but is never selected, so the trust it was
    meant for silently keeps using the shared key. Nothing else in the system says so.
    """
    gstt = _trust("GSTT")
    _set_trust_keys(monkeypatch, {f"trust-{uuid4()}": base64.b64encode(b"1" * 32).decode()})

    report = audit_trust_key_config(_session([gstt]))

    assert report.covered == []
    assert report.on_shared_key == ["GSTT"]
    assert len(report.orphan_kids) == 1


def test_shared_kid_is_never_an_orphan():
    report = audit_trust_key_config(_session([_trust("GSTT")]))
    assert encryption.SHARED_KID not in report.orphan_kids


def test_key_of_the_wrong_length_is_flagged(monkeypatch):
    gstt = _trust("GSTT")
    _set_trust_keys(monkeypatch, {kid_for_trust_id(gstt.id): base64.b64encode(b"too-short").decode()})

    report = audit_trust_key_config(_session([gstt]))

    assert report.covered == ["GSTT"]  # it is loaded...
    assert report.bad_length_kids == [kid_for_trust_id(gstt.id)]  # ...but unusable


def test_malformed_trust_keys_raises_so_boot_fails(monkeypatch):
    """A bad AES_TRUST_KEYS must fail at startup, not on the first request to encrypt."""
    monkeypatch.setenv("AES_TRUST_KEYS", "{not json")
    encryption._reset_caches()

    with pytest.raises(json.JSONDecodeError):
        audit_trust_key_config(_session([_trust("GSTT")]))


def test_log_reports_orphans_at_error_level(monkeypatch, caplog):
    _set_trust_keys(monkeypatch, {"trust-typo": base64.b64encode(b"1" * 32).decode()})

    with caplog.at_level(logging.INFO, logger="uvicorn"):
        report = log_trust_key_config(_session([_trust("GSTT")]))

    assert report.orphan_kids == ["trust-typo"]
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "an orphan kid must be logged at error level"
    assert "trust-typo" in errors[0].getMessage()


def test_log_does_not_raise_when_everything_is_on_the_shared_key(caplog):
    """A staged rollout is the normal state — coverage alone is not an error."""
    with caplog.at_level(logging.INFO, logger="uvicorn"):
        log_trust_key_config(_session([_trust("GSTT"), _trust("KCH")]))

    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
