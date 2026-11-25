# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

"""Tests for in-place trust key rotation (FLIP#1569)."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from flip_api.db.models.main_models import FLKitSlot, Trust, TrustsAudit
from flip_api.domain.schemas.actions import TrustAuditAction
from flip_api.trusts_services.services.register_trust import (
    TrustRegistrationError,
    issue_trust_keys,
    rotate_trust_keys,
)


def _trust(name: str = "GSTT", code: str = "GSTT") -> Trust:
    return Trust(id=uuid4(), name=name, code=code, api_key_hash="old-hash")  # pragma: allowlist secret


def _session(*, by_name: Trust | None = None, slot: FLKitSlot | None = None):
    """``session.exec`` is hit twice: the trust lookup, then the FL kit slot lookup."""
    session = MagicMock()
    session.exec.side_effect = [
        MagicMock(first=MagicMock(return_value=by_name)),
        MagicMock(first=MagicMock(return_value=slot)),
    ]
    return session


@patch("flip_api.trusts_services.services.register_trust.trust_key_cache")
@patch("flip_api.trusts_services.services.register_trust.generate_trust_key")
def test_revoke_old_moves_the_hash_at_once_and_returns_new_plaintext_keys(mock_gen_key, mock_cache):
    """REVOKE_OLD: the hash moves, the new keys come out once, and the old key stops working."""
    mock_gen_key.side_effect = [("new-api", "new-api-hash"), ("new-internal", "discarded")]
    trust = _trust()
    trust.pending_api_key_hash = "an-earlier-staged-hash"  # pragma: allowlist secret
    slot = FLKitSlot(slot_name="Trust_001", slot_number=1, assigned_to_trust_id=trust.id)
    session = _session(by_name=trust, slot=slot)

    result = rotate_trust_keys(session, name="GSTT", revoke_old=True)

    assert result.trust is trust
    assert trust.api_key_hash == "new-api-hash"  # pragma: allowlist secret
    assert trust.pending_api_key_hash is None
    assert trust.pending_api_key_expires_at is None
    assert result.trust_api_key == "new-api"  # pragma: allowlist secret
    assert result.trust_internal_service_key == "new-internal"  # pragma: allowlist secret
    assert result.fl_kit_slot is slot
    session.commit.assert_called_once()
    session.refresh.assert_called_once_with(trust)


@patch("flip_api.trusts_services.services.register_trust.trust_key_cache")
@patch("flip_api.trusts_services.services.register_trust.generate_trust_key")
def test_writes_one_rotated_audit_row_with_no_user(mock_gen_key, mock_cache):
    """The audit row names ROTATED, so the registry shows when credentials changed.

    The deploy CLI is the only caller and runs under operator IAM rather than as a FLIP user,
    so the row carries no user id.
    """
    mock_gen_key.side_effect = [("a", "b"), ("c", "d")]
    trust = _trust()
    session = _session(by_name=trust, slot=FLKitSlot(slot_name="Trust_001", slot_number=1))

    rotate_trust_keys(session, name="GSTT")

    audit_rows = [c.args[0] for c in session.add.call_args_list if isinstance(c.args[0], TrustsAudit)]
    assert len(audit_rows) == 1
    assert audit_rows[0].action == TrustAuditAction.ROTATED
    assert audit_rows[0].trust_id == trust.id
    assert audit_rows[0].modified_by_user_id is None


def test_refuses_unknown_trust():
    session = _session(by_name=None)

    with pytest.raises(TrustRegistrationError, match="No trust named"):
        rotate_trust_keys(session, name="NOPE")
    session.commit.assert_not_called()


@patch("flip_api.trusts_services.services.register_trust.generate_trust_key")
def test_refuses_rotation_without_a_kit_slot(mock_gen_key):
    """No slot means no kit to write the new keys into, so nothing rotates."""
    trust = _trust()
    session = _session(by_name=trust, slot=None)

    with pytest.raises(TrustRegistrationError, match="no FL kit slot"):
        rotate_trust_keys(session, name="GSTT")
    assert trust.api_key_hash == "old-hash"  # pragma: allowlist secret
    session.commit.assert_not_called()


@patch("flip_api.trusts_services.services.register_trust.trust_key_cache")
@patch("flip_api.trusts_services.services.register_trust.generate_trust_key")
def test_staged_by_default_the_live_key_keeps_working(mock_gen_key, mock_cache):
    """FLIP#1714: the new hash is staged next to the live one with a deadline; the live hash is untouched."""
    mock_gen_key.side_effect = [("new-api", "new-api-hash"), ("new-internal", "discarded")]
    trust = _trust()
    session = _session(by_name=trust, slot=FLKitSlot(slot_name="Trust_001", slot_number=1))
    before = datetime.now(timezone.utc)

    result = rotate_trust_keys(session, name="GSTT", grace=timedelta(days=3))

    assert trust.api_key_hash == "old-hash"  # pragma: allowlist secret
    assert trust.pending_api_key_hash == "new-api-hash"  # pragma: allowlist secret
    expires_at = trust.pending_api_key_expires_at
    assert before + timedelta(days=3) <= expires_at <= datetime.now(timezone.utc) + timedelta(days=3)
    assert result.trust_api_key == "new-api"  # pragma: allowlist secret
    session.commit.assert_called_once()


@patch("flip_api.trusts_services.services.register_trust.trust_key_cache")
@patch("flip_api.trusts_services.services.register_trust.generate_trust_key")
def test_rotating_again_while_staged_replaces_the_pending_key(mock_gen_key, mock_cache):
    """A lost kit is recovered by rotating again: the site still runs on its live key throughout."""
    mock_gen_key.side_effect = [("second", "second-hash"), ("internal", "discarded")]
    trust = _trust()
    trust.pending_api_key_hash = "first-hash"  # pragma: allowlist secret
    session = _session(by_name=trust, slot=FLKitSlot(slot_name="Trust_001", slot_number=1))

    first_deadline = datetime.now(timezone.utc) + timedelta(hours=1)
    trust.pending_api_key_expires_at = first_deadline
    before = datetime.now(timezone.utc)

    rotate_trust_keys(session, name="GSTT", grace=timedelta(days=3))

    assert trust.api_key_hash == "old-hash"  # pragma: allowlist secret
    assert trust.pending_api_key_hash == "second-hash"  # pragma: allowlist secret
    # The deadline restarts from the second rotation, not the first.
    assert trust.pending_api_key_expires_at >= before + timedelta(days=3)


@patch("flip_api.trusts_services.services.register_trust.trust_key_cache")
@patch("flip_api.trusts_services.services.register_trust.generate_trust_key")
def test_a_ui_issue_after_a_staged_rotation_drops_the_staged_key(mock_gen_key, mock_cache):
    """FLIP#1852's **Issue new keys** is the leak response: it must revoke a key staged earlier too."""
    mock_gen_key.side_effect = [("ui-key", "ui-hash"), ("internal", "discarded")]
    trust = _trust()
    trust.pending_api_key_hash = "staged-hash"  # pragma: allowlist secret
    trust.pending_api_key_expires_at = datetime.now(timezone.utc) + timedelta(days=14)
    slot = FLKitSlot(slot_name="Trust_001", slot_number=1, assigned_to_trust_id=trust.id)

    result = issue_trust_keys(MagicMock(), trust=trust, slot=slot, action=TrustAuditAction.ROTATED, user_id=uuid4())

    assert trust.api_key_hash == "ui-hash"  # pragma: allowlist secret
    assert trust.pending_api_key_hash is None
    assert trust.pending_api_key_expires_at is None
    assert result.staged_until is None


@patch("flip_api.trusts_services.services.register_trust.trust_key_cache")
@patch("flip_api.trusts_services.services.register_trust.generate_trust_key")
def test_a_staged_rotation_of_a_trust_with_no_key_yet_issues_the_key_directly(mock_gen_key, mock_cache):
    """A trust added in the UI has no live key until its kit is downloaded; staging would leave it with none."""
    mock_gen_key.side_effect = [("first", "first-hash"), ("internal", "discarded")]
    trust = Trust(id=uuid4(), name="GSTT", code="GSTT", api_key_hash=None)
    session = _session(by_name=trust, slot=FLKitSlot(slot_name="Trust_001", slot_number=1))

    result = rotate_trust_keys(session, name="GSTT")

    assert trust.api_key_hash == "first-hash"  # pragma: allowlist secret
    assert trust.pending_api_key_hash is None
    assert result.staged_until is None


@patch("flip_api.trusts_services.services.register_trust.trust_key_cache")
@patch("flip_api.trusts_services.services.register_trust.generate_trust_key")
def test_a_staged_rotation_reports_its_deadline(mock_gen_key, mock_cache):
    """The CLI prints which mode ran from ``staged_until``."""
    mock_gen_key.side_effect = [("a", "b"), ("c", "d")]
    trust = _trust()
    session = _session(by_name=trust, slot=FLKitSlot(slot_name="Trust_001", slot_number=1))

    result = rotate_trust_keys(session, name="GSTT", grace=timedelta(days=2))

    assert result.staged_until == trust.pending_api_key_expires_at


@patch("flip_api.trusts_services.services.register_trust.get_settings")
@patch("flip_api.trusts_services.services.register_trust.trust_key_cache")
@patch("flip_api.trusts_services.services.register_trust.generate_trust_key")
def test_the_default_deadline_comes_from_settings(mock_gen_key, mock_cache, mock_settings):
    mock_gen_key.side_effect = [("a", "b"), ("c", "d")]
    mock_settings.return_value.TRUST_KEY_ROTATION_GRACE_DAYS = 7
    trust = _trust()
    session = _session(by_name=trust, slot=FLKitSlot(slot_name="Trust_001", slot_number=1))

    rotate_trust_keys(session, name="GSTT")

    delta = trust.pending_api_key_expires_at - datetime.now(timezone.utc)
    assert timedelta(days=6, hours=23) < delta <= timedelta(days=7)
