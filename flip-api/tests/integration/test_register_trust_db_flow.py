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

"""register_trust's slot claim from the source's names, and key rotation, against real Postgres.

The unit tests mock the session, so the mechanics that make the claim safe —
``begin_nested()`` savepoint flush, rollback leaving the outer transaction usable,
and ``SELECT ... FOR UPDATE SKIP LOCKED`` seeing rows inserted in the same open
transaction but only those the source names — are only exercised here. Rotation is here for the same reason: whether
the hub really refuses the old key afterwards is a property of the committed row, not of a mock.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlmodel import Session, select

from flip_api.auth import trust_key_cache
from flip_api.auth.access_manager import _promote_pending_key, authenticate_trust
from flip_api.db.models.main_models import FLKitSlot, Trust, TrustsAudit
from flip_api.domain.schemas.actions import TrustAuditAction
from flip_api.trusts_services.expire_staged_trust_keys import expire_staged_trust_keys
from flip_api.trusts_services.services.register_trust import (
    NoFreeKitSlotError,
    issue_trust_keys,
    register_trust,
    rotate_trust_keys,
)


@patch("flip_api.trusts_services.services.register_trust.resolve_fl_kit_slot_names")
def test_new_slot_is_inserted_and_claimed_within_one_transaction(mock_resolve, session):
    """Empty pool + a newly named slot: the claim wins the just-inserted row, and the
    whole registration commits atomically.
    """
    mock_resolve.return_value = ["Trust_701"]

    result = register_trust(name="Reconcile Trust", code="RCT", region=None, session=session)

    assert result.fl_kit_slot.slot_name == "Trust_701"
    session.expire_all()
    slot = session.exec(select(FLKitSlot).where(FLKitSlot.slot_name == "Trust_701")).one()
    assert slot.assigned_to_trust_id == result.trust.id
    assert slot.assigned_at is not None


@patch("flip_api.trusts_services.services.register_trust.resolve_fl_kit_slot_names")
def test_exhausted_pool_raises_and_commits_nothing(mock_resolve, session):
    """The realistic exhaustion shape: the resolver keeps returning the same name,
    whose row is already assigned — nothing is inserted (the savepoint flush runs
    for real), the claim misses, and no partial trust row survives.
    """
    mock_resolve.return_value = ["Trust_702"]
    register_trust(name="First Trust", code="FST", region=None, session=session)

    with pytest.raises(NoFreeKitSlotError, match="No FL kit slots available"):
        register_trust(name="Second Trust", code="SND", region=None, session=session)

    session.rollback()  # clear the aborted registration's open transaction
    session.expire_all()
    assert session.exec(select(Trust).where(Trust.name == "Second Trust")).first() is None
    # The pool still holds exactly the one assigned row.
    slots = session.exec(select(FLKitSlot)).all()
    assert [s.slot_name for s in slots] == ["Trust_702"]
    assert slots[0].assigned_to_trust_id is not None


@patch("flip_api.trusts_services.services.register_trust.resolve_fl_kit_slot_names")
def test_free_slot_the_source_no_longer_names_is_not_claimed(mock_resolve, session):
    """A kit withdrawn after its row was seeded (deleted from S3, say) is not handed out,
    even though its row is free and lower-numbered.
    """
    session.add(FLKitSlot(slot_name="Trust_704", slot_number=704))
    session.commit()
    mock_resolve.return_value = ["Trust_705"]

    result = register_trust(name="Withdrawn Kit Trust", code="WKT", region=None, session=session)

    assert result.fl_kit_slot.slot_name == "Trust_705"
    session.expire_all()
    withdrawn = session.exec(select(FLKitSlot).where(FLKitSlot.slot_name == "Trust_704")).one()
    assert withdrawn.assigned_to_trust_id is None


@patch("flip_api.trusts_services.services.register_trust.resolve_fl_kit_slot_names")
def test_revoke_old_rotation_refuses_the_old_key_and_leaves_identity_untouched(mock_resolve, session):
    """After a REVOKE_OLD rotation the old key 401s, the new one authenticates, and only the hash moved."""
    mock_resolve.return_value = ["Trust_703"]
    registered = register_trust(name="Rotating Trust", code="ROT", region=None, session=session)
    trust_id, slot_name = registered.trust.id, registered.fl_kit_slot.slot_name
    old_key = registered.trust_api_key
    # The committed hash of the old key, read before the rotation moves it. Taking it from the
    # row rather than re-hashing here keeps the cache-seeding below on the production hash.
    old_key_hash = session.exec(select(Trust).where(Trust.id == trust_id)).one().api_key_hash
    assert old_key_hash is not None
    # Warm the auth cache on the old key: a cache hit must not survive the rotation either.
    assert authenticate_trust(api_key=old_key, db=session).id == trust_id

    try:
        rotated = rotate_trust_keys(session, name="Rotating Trust", revoke_old=True)

        assert rotated.trust_api_key != old_key
        with pytest.raises(HTTPException) as excinfo:
            authenticate_trust(api_key=old_key, db=session)
        assert excinfo.value.status_code == 401

        # rotate_trust_keys invalidates this process's cache, so the assertion above never
        # reaches the cache-hit branch. Every other hub worker keeps its warm entry, and the
        # live-hash re-check inside that branch is the only thing refusing the old key there.
        # Seed the entry the way another worker would still hold it, and retry.
        trust_key_cache.remember(old_key_hash, trust_id)
        with pytest.raises(HTTPException) as cached_excinfo:
            authenticate_trust(api_key=old_key, db=session)
        assert cached_excinfo.value.status_code == 401

        assert authenticate_trust(api_key=rotated.trust_api_key, db=session).id == trust_id

        # Identity and the slot assignment are untouched; the audit log is the only new row.
        assert rotated.trust.id == trust_id
        assert rotated.fl_kit_slot.slot_name == slot_name
        session.expire_all()
        slot = session.exec(select(FLKitSlot).where(FLKitSlot.slot_name == slot_name)).one()
        assert slot.assigned_to_trust_id == trust_id
        rotations = session.exec(
            select(TrustsAudit).where(TrustsAudit.trust_id == trust_id, TrustsAudit.action == TrustAuditAction.ROTATED)
        ).all()
        assert len(rotations) == 1
    finally:
        # A failed assertion must not leak a warm entry into the tests that follow.
        trust_key_cache.invalidate()


@patch("flip_api.trusts_services.services.register_trust.resolve_fl_kit_slot_names")
def test_staged_rotation_keeps_the_old_key_until_the_new_one_is_used(mock_resolve, session):
    """FLIP#1714 against real Postgres: both keys work, first use of the new key promotes it, then the old key 401s."""
    mock_resolve.return_value = ["Trust_704"]
    registered = register_trust(name="Staged Trust", code="STG", region=None, session=session)
    trust_id = registered.trust.id
    old_key = registered.trust_api_key
    old_key_hash = session.exec(select(Trust).where(Trust.id == trust_id)).one().api_key_hash

    try:
        rotated = rotate_trust_keys(session, name="Staged Trust")

        # Staged: the site is still online on its old key.
        assert authenticate_trust(api_key=old_key, db=session).id == trust_id
        session.expire_all()
        row = session.exec(select(Trust).where(Trust.id == trust_id)).one()
        assert row.api_key_hash == old_key_hash
        assert row.pending_api_key_hash is not None
        assert row.pending_api_key_expires_at is not None

        # First use of the new key promotes it, and that is committed.
        assert authenticate_trust(api_key=rotated.trust_api_key, db=session).id == trust_id
        session.expire_all()
        row = session.exec(select(Trust).where(Trust.id == trust_id)).one()
        assert row.pending_api_key_hash is None
        assert row.api_key_hash != old_key_hash

        # The old key is refused from now on, also through another worker's warm cache entry.
        with pytest.raises(HTTPException) as excinfo:
            authenticate_trust(api_key=old_key, db=session)
        assert excinfo.value.status_code == 401
        trust_key_cache.remember(old_key_hash, trust_id)
        with pytest.raises(HTTPException) as cached_excinfo:
            authenticate_trust(api_key=old_key, db=session)
        assert cached_excinfo.value.status_code == 401

        actions = [a.action for a in session.exec(select(TrustsAudit).where(TrustsAudit.trust_id == trust_id)).all()]
        assert TrustAuditAction.ROTATED in actions
        assert TrustAuditAction.KEY_PROMOTED in actions
    finally:
        trust_key_cache.invalidate()


def _audit_count(session, trust_id, action) -> int:
    session.expire_all()
    return len(
        session.exec(select(TrustsAudit).where(TrustsAudit.trust_id == trust_id, TrustsAudit.action == action)).all()
    )


def _register(session, mock_resolve, slot: str, name: str, code: str):
    mock_resolve.return_value = [slot]
    registered = register_trust(name=name, code=code, region=None, session=session)
    return registered, registered.trust.id


@patch("flip_api.trusts_services.services.register_trust.resolve_fl_kit_slot_names")
def test_a_staged_key_past_its_deadline_is_refused_and_the_job_drops_it_once(mock_resolve, session):
    """Old key works, new key 401s, and the scheduled job writes exactly one KEY_EXPIRED row."""
    registered, trust_id = _register(session, mock_resolve, "Trust_705", "Expiring Trust", "EXP")
    try:
        rotated = rotate_trust_keys(session, name="Expiring Trust", grace=timedelta(seconds=-1))

        assert authenticate_trust(api_key=registered.trust_api_key, db=session).id == trust_id
        with pytest.raises(HTTPException) as excinfo:
            authenticate_trust(api_key=rotated.trust_api_key, db=session)
        assert excinfo.value.status_code == 401
        assert _audit_count(session, trust_id, TrustAuditAction.KEY_EXPIRED) == 0  # auth writes nothing

        assert expire_staged_trust_keys(session) == 1
        assert expire_staged_trust_keys(session) == 0
        assert _audit_count(session, trust_id, TrustAuditAction.KEY_EXPIRED) == 1
        row = session.exec(select(Trust).where(Trust.id == trust_id)).one()
        assert row.pending_api_key_hash is None
        assert authenticate_trust(api_key=registered.trust_api_key, db=session).id == trust_id
    finally:
        trust_key_cache.invalidate()


@patch("flip_api.trusts_services.services.register_trust.resolve_fl_kit_slot_names")
def test_the_expiry_job_leaves_a_live_staged_key_alone(mock_resolve, session):
    _, trust_id = _register(session, mock_resolve, "Trust_706", "Live Staged Trust", "LST")
    rotate_trust_keys(session, name="Live Staged Trust")

    assert expire_staged_trust_keys(session) == 0
    session.expire_all()
    assert session.exec(select(Trust).where(Trust.id == trust_id)).one().pending_api_key_hash is not None


@patch("flip_api.trusts_services.services.register_trust.resolve_fl_kit_slot_names")
def test_two_first_calls_with_the_new_key_write_one_promotion(mock_resolve, session, integration_engine):
    """Both callers read the row while the key was still staged; only the one that wins the update audits."""
    _, trust_id = _register(session, mock_resolve, "Trust_707", "Racing Trust", "RAC")
    try:
        rotated = rotate_trust_keys(session, name="Racing Trust")
        new_hash = session.exec(select(Trust).where(Trust.id == trust_id)).one().pending_api_key_hash
        now = datetime.now(timezone.utc)
        with Session(integration_engine) as first, Session(integration_engine) as second:
            stale_a = first.get(Trust, trust_id)
            stale_b = second.get(Trust, trust_id)
            assert stale_a.pending_api_key_hash == stale_b.pending_api_key_hash == new_hash

            assert _promote_pending_key(stale_a, new_hash, first, now) is True
            assert _promote_pending_key(stale_b, new_hash, second, now) is True

        assert authenticate_trust(api_key=rotated.trust_api_key, db=session).id == trust_id
        assert _audit_count(session, trust_id, TrustAuditAction.KEY_PROMOTED) == 1
    finally:
        trust_key_cache.invalidate()


@patch("flip_api.trusts_services.services.register_trust.resolve_fl_kit_slot_names")
def test_a_revoke_between_read_and_promotion_wins(mock_resolve, session, integration_engine):
    """A --revoke-old that commits after the auth sweep read the row is not overwritten by the staged key."""
    _, trust_id = _register(session, mock_resolve, "Trust_708", "Revoked Trust", "REV")
    try:
        staged = rotate_trust_keys(session, name="Revoked Trust")
        staged_hash = session.exec(select(Trust).where(Trust.id == trust_id)).one().pending_api_key_hash
        with Session(integration_engine) as auth:
            stale = auth.get(Trust, trust_id)  # the auth sweep's read
            revoked = rotate_trust_keys(session, name="Revoked Trust", revoke_old=True)

            assert _promote_pending_key(stale, staged_hash, auth, datetime.now(timezone.utc)) is False

        with pytest.raises(HTTPException):
            authenticate_trust(api_key=staged.trust_api_key, db=session)
        assert authenticate_trust(api_key=revoked.trust_api_key, db=session).id == trust_id
        assert _audit_count(session, trust_id, TrustAuditAction.KEY_PROMOTED) == 0
    finally:
        trust_key_cache.invalidate()


@patch("flip_api.trusts_services.services.register_trust.resolve_fl_kit_slot_names")
def test_a_ui_issue_revokes_a_key_staged_before_it(mock_resolve, session):
    """FLIP#1852's UI rotate after a CLI staged rotation: the staged key must not survive to replace it."""
    _, trust_id = _register(session, mock_resolve, "Trust_709", "UI Trust", "UIT")
    try:
        staged = rotate_trust_keys(session, name="UI Trust")
        trust = session.exec(select(Trust).where(Trust.id == trust_id)).one()
        slot = session.exec(select(FLKitSlot).where(FLKitSlot.assigned_to_trust_id == trust_id)).one()
        ui = issue_trust_keys(session, trust=trust, slot=slot, action=TrustAuditAction.ROTATED, user_id=None)

        with pytest.raises(HTTPException):
            authenticate_trust(api_key=staged.trust_api_key, db=session)
        assert authenticate_trust(api_key=ui.trust_api_key, db=session).id == trust_id
        assert _audit_count(session, trust_id, TrustAuditAction.KEY_PROMOTED) == 0
    finally:
        trust_key_cache.invalidate()


@patch("flip_api.trusts_services.services.register_trust.resolve_fl_kit_slot_names")
def test_a_staged_rotation_of_a_trust_without_keys_issues_a_working_key(mock_resolve, session):
    """A trust added in the UI has no live key; a staged rotation must still leave it a key that works."""
    mock_resolve.return_value = ["Trust_710"]
    registered = register_trust(name="Keyless Trust", code="KEY", region=None, session=session, issue_keys=False)
    try:
        rotated = rotate_trust_keys(session, name="Keyless Trust")

        assert rotated.staged_until is None
        assert authenticate_trust(api_key=rotated.trust_api_key, db=session).id == registered.trust.id
    finally:
        trust_key_cache.invalidate()
