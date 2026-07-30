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

"""register_trust's slot claim + reconcile-on-miss against real Postgres.

The unit tests mock the session, so the mechanics that make the reconcile safe —
``begin_nested()`` savepoint flush, rollback leaving the outer transaction usable,
and the retried ``SELECT ... FOR UPDATE SKIP LOCKED`` seeing rows inserted in the
same open transaction — are only exercised here.
"""

from unittest.mock import patch

import pytest
from sqlmodel import select

from flip_api.db.models.main_models import FLKitSlot, Trust
from flip_api.trusts_services.services.register_trust import NoFreeKitSlotError, register_trust


@patch("flip_api.trusts_services.services.register_trust.resolve_fl_kit_slot_names")
def test_reconcile_grows_pool_and_claims_within_one_transaction(mock_resolve, session):
    """Empty pool + a newly configured name: the miss reconciles, the retried claim
    wins the just-inserted row, and the whole registration commits atomically.
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
    whose row is already assigned — reconcile inserts nothing (the savepoint flush
    runs for real), the retry misses, and no partial trust row survives.
    """
    mock_resolve.return_value = ["Trust_702"]
    register_trust(name="First Trust", code="FST", region=None, session=session)

    with pytest.raises(NoFreeKitSlotError, match="No FL kit slots available"):
        register_trust(name="Second Trust", code="SND", region=None, session=session)

    session.rollback()  # clear the aborted registration's open transaction
    session.expire_all()
    assert session.exec(select(Trust).where(Trust.name == "Second Trust")).first() is None
    # The pool still holds exactly the one assigned row — reconcile stayed additive.
    slots = session.exec(select(FLKitSlot)).all()
    assert [s.slot_name for s in slots] == ["Trust_702"]
    assert slots[0].assigned_to_trust_id is not None
