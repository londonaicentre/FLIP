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
from uuid import uuid4

import pytest

from flip_api.db.models.main_models import FLKitSlot, Trust
from flip_api.trusts_services.services.register_trust import (
    DuplicateTrustError,
    EmptyTrustNameError,
    NoFreeKitSlotError,
    register_trust,
)


def _mock_session(*, existing_trust: Trust | None, free_slot: FLKitSlot | None) -> MagicMock:
    """``session.exec`` is hit twice: the duplicate-name lookup, then the slot claim."""
    session = MagicMock()
    session.exec.side_effect = [
        MagicMock(first=MagicMock(return_value=existing_trust)),
        MagicMock(first=MagicMock(return_value=free_slot)),
    ]
    return session


@patch("flip_api.trusts_services.services.register_trust.generate_trust_key")
def test_register_trust_creates_trust_and_claims_slot(mock_gen_key):
    mock_gen_key.side_effect = [("plain-api", "hash-api"), ("plain-internal", "hash-internal")]
    slot = FLKitSlot(slot_name="Trust_007", slot_number=7)
    session = _mock_session(existing_trust=None, free_slot=slot)

    result = register_trust(name="GSTT", code="GSTT", region="London", session=session)

    # The new trust carries the hashed api key, not the plaintext.
    assert result.trust.name == "GSTT"
    assert result.trust.code == "GSTT"
    assert result.trust.region == "London"
    assert result.trust.api_key_hash == "hash-api"
    # The slot is now bound to the new trust, with an assignment timestamp.
    assert slot.assigned_to_trust_id == result.trust.id
    assert slot.assigned_at is not None
    # Plaintext keys come out exactly once.
    assert result.trust_api_key == "plain-api"
    assert result.trust_internal_service_key == "plain-internal"
    assert result.fl_kit_slot.slot_name == "Trust_007"
    # Atomic: one commit covering the trust insert + the audit row write.
    # Two flushes: one to populate trust.id before slot binding, one inside
    # audit_trust_action.
    assert session.flush.call_count == 2
    session.commit.assert_called_once()


@patch("flip_api.trusts_services.services.register_trust.generate_trust_key")
def test_register_trust_writes_audit_row_with_user_id(mock_gen_key):
    """The UI path passes ``audit_user_id`` — the audit row's
    ``modified_by_user_id`` matches the authenticated admin's id.
    """
    from flip_api.db.models.main_models import TrustsAudit
    from flip_api.domain.schemas.actions import TrustAuditAction

    mock_gen_key.side_effect = [("k", "h"), ("k", "h")]
    slot = FLKitSlot(slot_name="Trust_007", slot_number=7)
    session = _mock_session(existing_trust=None, free_slot=slot)
    admin_id = uuid4()

    register_trust(name="GSTT", code=None, region=None, session=session, audit_user_id=admin_id)

    audit_calls = [
        call for call in session.add.call_args_list if isinstance(call.args[0], TrustsAudit)
    ]
    assert len(audit_calls) == 1
    audit_row = audit_calls[0].args[0]
    assert audit_row.action == TrustAuditAction.REGISTERED
    assert audit_row.modified_by_user_id == admin_id
    assert audit_row.trust_name == "GSTT"


@patch("flip_api.trusts_services.services.register_trust.generate_trust_key")
def test_register_trust_writes_audit_row_with_null_user_for_cli(mock_gen_key):
    """The deploy-CLI path passes no ``audit_user_id`` — the audit row's
    ``modified_by_user_id`` is NULL.
    """
    from flip_api.db.models.main_models import TrustsAudit

    mock_gen_key.side_effect = [("k", "h"), ("k", "h")]
    slot = FLKitSlot(slot_name="Trust_007", slot_number=7)
    session = _mock_session(existing_trust=None, free_slot=slot)

    register_trust(name="GSTT", code=None, region=None, session=session)

    audit_calls = [
        call for call in session.add.call_args_list if isinstance(call.args[0], TrustsAudit)
    ]
    assert len(audit_calls) == 1
    assert audit_calls[0].args[0].modified_by_user_id is None


@patch("flip_api.trusts_services.services.register_trust.generate_trust_key")
def test_register_trust_strips_whitespace(mock_gen_key):
    mock_gen_key.side_effect = [("k", "h"), ("k", "h")]
    slot = FLKitSlot(slot_name="x", slot_number=0)
    session = _mock_session(existing_trust=None, free_slot=slot)

    result = register_trust(name="  GSTT  ", code="  GSTT  ", region="  London  ", session=session)

    assert result.trust.name == "GSTT"
    assert result.trust.code == "GSTT"
    assert result.trust.region == "London"


def test_register_trust_rejects_blank_name():
    session = MagicMock()
    with pytest.raises(EmptyTrustNameError):
        register_trust(name="   ", code=None, region=None, session=session)
    # Validation fires before any DB work.
    session.exec.assert_not_called()


def test_register_trust_409_on_duplicate_name():
    existing = Trust(id=uuid4(), name="GSTT")
    session = _mock_session(existing_trust=existing, free_slot=None)

    with pytest.raises(DuplicateTrustError, match="already exists"):
        register_trust(name="GSTT", code=None, region=None, session=session)

    # The slot lookup never runs — duplicate is checked first.
    assert session.exec.call_count == 1
    session.commit.assert_not_called()


@patch("flip_api.trusts_services.services.register_trust.generate_trust_key")
def test_register_trust_409_when_pool_exhausted(mock_gen_key):
    mock_gen_key.side_effect = [("k", "h"), ("k", "h")]
    session = _mock_session(existing_trust=None, free_slot=None)

    with pytest.raises(NoFreeKitSlotError, match="No FL kit slots available"):
        register_trust(name="GSTT", code=None, region=None, session=session)

    session.commit.assert_not_called()
    # No trust insert without a slot to back it.
    session.add.assert_not_called()
