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
    EmptyTrustCodeError,
    EmptyTrustNameError,
    InvalidTrustError,
    KitSlotSourceUnreadableError,
    NoFreeKitSlotError,
    register_trust,
)


@pytest.fixture(autouse=True)
def mock_resolve():
    """The slot source. It names nothing unless a test says so, which keeps the row
    lookup of ``insert_missing_slots`` out of ``session.exec``.
    """
    with patch(
        "flip_api.trusts_services.services.register_trust.resolve_fl_kit_slot_names", return_value=[]
    ) as resolve:
        yield resolve


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
    assert result.trust.api_key_hash == "hash-api"  # pragma: allowlist secret
    # The slot is now bound to the new trust, with an assignment timestamp.
    assert slot.assigned_to_trust_id == result.trust.id
    assert slot.assigned_at is not None
    # Plaintext keys come out exactly once.
    assert result.trust_api_key == "plain-api"  # pragma: allowlist secret
    assert result.trust_internal_service_key == "plain-internal"  # pragma: allowlist secret
    assert result.fl_kit_slot.slot_name == "Trust_007"
    # Atomic: one commit covering the trust insert + the audit row write.
    # Two flushes: one to populate trust.id before slot binding, one inside
    # audit_trust_action.
    assert session.flush.call_count == 2
    session.commit.assert_called_once()


@patch("flip_api.trusts_services.services.register_trust.generate_trust_key")
def test_register_trust_without_keys_leaves_the_trust_unable_to_authenticate(mock_gen_key):
    """The UI path (FLIP#1852): the slot is claimed, no key is minted, and the hash stays NULL."""
    slot = FLKitSlot(slot_name="Trust_007", slot_number=7)
    session = _mock_session(existing_trust=None, free_slot=slot)

    result = register_trust(name="GSTT", code="GSTT", region=None, session=session, issue_keys=False)

    mock_gen_key.assert_not_called()
    assert result.trust.api_key_hash is None
    assert result.trust_api_key is None
    assert result.trust_internal_service_key is None
    assert slot.assigned_to_trust_id == result.trust.id


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

    register_trust(name="GSTT", code="GSTT", region=None, session=session, audit_user_id=admin_id)

    audit_calls = [call for call in session.add.call_args_list if isinstance(call.args[0], TrustsAudit)]
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

    register_trust(name="GSTT", code="GSTT", region=None, session=session)

    audit_calls = [call for call in session.add.call_args_list if isinstance(call.args[0], TrustsAudit)]
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
        register_trust(name="   ", code="GSTT", region=None, session=session)
    # Validation fires before any DB work.
    session.exec.assert_not_called()


@pytest.mark.parametrize(
    ("name", "code", "region"),
    [("St Thomas", "ST T", None), ("St Thomas", "../etc", None), ("St $(id)", "STT", None), ("St Thomas", "STT", "`")],
)
def test_register_trust_rejects_what_a_kit_file_could_not_carry(name, code, region):
    # Such a trust could never be given a kit, so never keys (FLIP#1852).
    session = MagicMock()
    with pytest.raises(InvalidTrustError):
        register_trust(name=name, code=code, region=region, session=session)
    session.exec.assert_not_called()


@pytest.mark.parametrize(("name", "region"), [("St Thomas (EC2)", None), ("Trust_1", None), ("St Thomas", "N/E")])
def test_register_trust_rejects_other_symbols(name, region):
    # The same rule as the Add Trust form: letters, digits, spaces, ', & and -.
    session = MagicMock()
    with pytest.raises(InvalidTrustError, match="isn't allowed"):
        register_trust(name=name, code="STT", region=region, session=session)
    session.exec.assert_not_called()


def test_register_trust_accepts_apostrophes_ampersands_hyphens_and_accents():
    slot = FLKitSlot(slot_name="Trust_007", slot_number=7)
    session = _mock_session(existing_trust=None, free_slot=slot)

    result = register_trust(
        name="Guy’s & St Thomas' Trust", code="GSTT", region="North-East Île de France", session=session
    )

    assert result.trust.name == "Guy’s & St Thomas' Trust"


@pytest.mark.parametrize("code", ["   ", "", None])
def test_register_trust_rejects_blank_or_missing_code(code):
    # code is now required at registration — a blank/None code is rejected before
    # any DB work, so a trust can never be registered without one.
    session = MagicMock()
    with pytest.raises(EmptyTrustCodeError):
        register_trust(name="GSTT", code=code, region=None, session=session)
    session.exec.assert_not_called()


def test_register_trust_409_on_duplicate_name():
    existing = Trust(id=uuid4(), name="GSTT")
    session = _mock_session(existing_trust=existing, free_slot=None)

    with pytest.raises(DuplicateTrustError, match="already exists"):
        register_trust(name="GSTT", code="GSTT", region=None, session=session)

    # The slot lookup never runs — duplicate is checked first.
    assert session.exec.call_count == 1
    session.commit.assert_not_called()


@patch("flip_api.trusts_services.services.register_trust.generate_trust_key")
def test_register_trust_409_when_the_source_names_no_slot(mock_gen_key, mock_resolve):
    """An empty slot source: nothing to claim, so 409 and no trust row."""
    mock_gen_key.side_effect = [("k", "h"), ("k", "h")]
    session = _mock_session(existing_trust=None, free_slot=None)

    with pytest.raises(NoFreeKitSlotError, match="No FL kit slots available"):
        register_trust(name="GSTT", code="GSTT", region=None, session=session)

    mock_resolve.assert_called_once()
    session.commit.assert_not_called()
    # No trust insert without a slot to back it.
    session.add.assert_not_called()


def test_register_trust_refuses_when_the_source_is_unreadable(mock_resolve):
    """Free slots may exist, so the admin is told to retry, not to mint kits. Still fails closed."""
    mock_resolve.return_value = None
    session = _mock_session(existing_trust=None, free_slot=None)

    with pytest.raises(KitSlotSourceUnreadableError, match="could not be read"):
        register_trust(name="GSTT", code="GSTT", region=None, session=session)

    assert session.exec.call_count == 1  # the duplicate-name lookup only: no claim
    session.add.assert_not_called()
    session.commit.assert_not_called()


@patch("flip_api.trusts_services.services.register_trust.generate_trust_key")
def test_register_trust_409_when_all_named_slots_are_assigned(mock_gen_key, mock_resolve):
    """The realistic prod exhaustion: every slot the source names already has a row, and
    every row is assigned.
    """
    mock_gen_key.side_effect = [("k", "h"), ("k", "h")]
    mock_resolve.return_value = ["Trust_1"]
    assigned = FLKitSlot(slot_name="Trust_1", slot_number=1)
    assigned.assigned_to_trust_id = uuid4()
    session = MagicMock()
    session.exec.side_effect = [
        MagicMock(first=MagicMock(return_value=None)),  # duplicate-name lookup
        MagicMock(all=MagicMock(return_value=["Trust_1"])),  # insert_missing_slots: row exists
        MagicMock(first=MagicMock(return_value=None)),  # slot claim misses
    ]

    with pytest.raises(NoFreeKitSlotError, match="No FL kit slots available"):
        register_trust(name="GSTT", code="GSTT", region=None, session=session)

    session.commit.assert_not_called()
    session.add.assert_not_called()


@patch("flip_api.trusts_services.services.register_trust.generate_trust_key")
def test_register_trust_claims_a_slot_added_since_boot(mock_gen_key, mock_resolve):
    """The source grew since boot (e.g. a kit was minted): the registration inserts the
    new row and claims it, with no restart.
    """
    mock_gen_key.side_effect = [("plain-api", "hash-api"), ("plain-internal", "hash-internal")]
    mock_resolve.return_value = ["Trust_1", "Trust_2", "Trust_3"]
    new_slot = FLKitSlot(slot_name="Trust_3", slot_number=3)
    session = MagicMock()
    session.exec.side_effect = [
        MagicMock(first=MagicMock(return_value=None)),  # duplicate-name lookup
        # insert_missing_slots existence check: Trust_1/Trust_2 exist, Trust_3 doesn't
        MagicMock(all=MagicMock(return_value=["Trust_1", "Trust_2"])),
        MagicMock(first=MagicMock(return_value=new_slot)),  # slot claim
    ]

    result = register_trust(name="BDMS", code="BDMS", region=None, session=session)

    assert result.fl_kit_slot.slot_name == "Trust_3"
    assert new_slot.assigned_to_trust_id == result.trust.id
    inserted = [c.args[0] for c in session.add.call_args_list if isinstance(c.args[0], FLKitSlot)]
    assert [s.slot_name for s in inserted] == ["Trust_3"]
    session.commit.assert_called_once()


@patch("flip_api.trusts_services.services.register_trust.generate_trust_key")
def test_register_trust_tolerates_concurrent_slot_insert(mock_gen_key, mock_resolve):
    """Two registrations insert the same new slot row at once: the loser's savepoint hits
    the slot_name PK (IntegrityError) — swallowed, and the claim still proceeds.
    """
    from sqlalchemy.exc import IntegrityError

    mock_gen_key.side_effect = [("k", "h"), ("k", "h")]
    mock_resolve.return_value = ["Trust_3"]
    won_slot = FLKitSlot(slot_name="Trust_3", slot_number=3)
    session = MagicMock()
    # The savepoint's exit (flush/commit) raises: the concurrent winner inserted Trust_3 first.
    session.begin_nested.return_value.__exit__.side_effect = IntegrityError("stmt", {}, Exception("duplicate pk"))
    session.exec.side_effect = [
        MagicMock(first=MagicMock(return_value=None)),  # duplicate-name lookup
        MagicMock(all=MagicMock(return_value=[])),  # insert_missing_slots existence check
        MagicMock(first=MagicMock(return_value=won_slot)),  # slot claim sees the winner's row
    ]

    result = register_trust(name="BDMS", code="BDMS", region=None, session=session)

    assert result.fl_kit_slot.slot_name == "Trust_3"
    assert won_slot.assigned_to_trust_id == result.trust.id
    added_trusts = [c.args[0] for c in session.add.call_args_list if isinstance(c.args[0], Trust)]
    assert [t.name for t in added_trusts] == ["BDMS"]
    session.commit.assert_called_once()
