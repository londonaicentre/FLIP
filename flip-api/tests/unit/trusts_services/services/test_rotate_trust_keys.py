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

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from flip_api.db.models.main_models import FLKitSlot, Trust, TrustsAudit
from flip_api.domain.schemas.actions import TrustAuditAction
from flip_api.trusts_services.services.register_trust import (
    TrustRegistrationError,
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
def test_rotates_hash_and_returns_new_plaintext_keys(mock_gen_key, mock_cache):
    """The hash moves, the new keys come out once, and the old plaintext is gone."""
    mock_gen_key.side_effect = [("new-api", "new-api-hash"), ("new-internal", "discarded")]
    trust = _trust()
    slot = FLKitSlot(slot_name="Trust_001", slot_number=1, assigned_to_trust_id=trust.id)
    session = _session(by_name=trust, slot=slot)

    result = rotate_trust_keys(session, name="GSTT")

    assert result.trust is trust
    assert trust.api_key_hash == "new-api-hash"  # pragma: allowlist secret
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
