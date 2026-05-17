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
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException, status

from flip_api.db.models.main_models import FLKitSlot, Trust
from flip_api.domain.interfaces.trust import ICreateTrust
from flip_api.trusts_services.admin_create_trust import admin_create_trust


@pytest.fixture
def admin_id() -> UUID:
    return uuid4()


def _mock_db(*, slot: FLKitSlot | None, existing_trust: Trust | None = None) -> MagicMock:
    """Build a db.exec mock that returns the given existing_trust then the given free slot.

    The endpoint hits .exec twice before doing the insert: first the name-clash lookup,
    then the slot-claim SELECT ... FOR UPDATE.
    """
    db = MagicMock()
    db.exec.side_effect = [
        MagicMock(first=MagicMock(return_value=existing_trust)),  # name clash check
        MagicMock(first=MagicMock(return_value=slot)),  # slot claim
    ]
    return db


@patch("flip_api.trusts_services.admin_create_trust.has_permissions", return_value=True)
@patch("flip_api.trusts_services.admin_create_trust.generate_trust_key")
def test_admin_create_trust_assigns_next_free_slot(mock_gen_key, _mock_perms, admin_id):  # noqa: PT019  -- patch-injected, not a fixture
    mock_gen_key.side_effect = [("plain-api", "hash-api"), ("plain-internal", "hash-internal")]

    free_slot = FLKitSlot(slot_name="Trust_007", slot_number=7)
    db = _mock_db(slot=free_slot)

    result = admin_create_trust(
        body=ICreateTrust(name="GSTT", code="GSTT", region="London"),
        db=db,
        token_id=admin_id,
    )

    # Trust + slot are persisted in the same transaction. db.add called twice (trust, slot
    # is already attached to session so just a row update — but the call count check on
    # db.add isn't load-bearing, the assignment is what matters).
    assert free_slot.assigned_to_trust_id == result.id
    assert free_slot.assigned_at is not None
    db.commit.assert_called_once()
    db.rollback.assert_not_called()

    # Response carries the slot identity the operator needs.
    assert result.name == "GSTT"
    assert result.fl_kit_slot == "Trust_007"
    assert result.fl_kit_slot_number == 7
    assert result.trust_api_key == "plain-api"
    assert result.trust_internal_service_key == "plain-internal"


@patch("flip_api.trusts_services.admin_create_trust.has_permissions", return_value=True)
@patch("flip_api.trusts_services.admin_create_trust.generate_trust_key")
def test_admin_create_trust_409_when_pool_exhausted(mock_gen_key, _mock_perms, admin_id):  # noqa: PT019  -- patch-injected, not a fixture
    mock_gen_key.side_effect = [("plain-api", "hash-api"), ("plain-internal", "hash-internal")]

    db = _mock_db(slot=None)

    with pytest.raises(HTTPException) as exc_info:
        admin_create_trust(
            body=ICreateTrust(name="GSTT"),
            db=db,
            token_id=admin_id,
        )

    assert exc_info.value.status_code == status.HTTP_409_CONFLICT
    assert "FL kit slots" in exc_info.value.detail
    # No trust row should be persisted if there's no slot to back it.
    db.rollback.assert_called_once()
    db.commit.assert_not_called()


@patch("flip_api.trusts_services.admin_create_trust.has_permissions", return_value=False)
def test_admin_create_trust_403_without_admin_permission(_mock_perms, admin_id):  # noqa: PT019  -- patch-injected, not a fixture
    with pytest.raises(HTTPException) as exc_info:
        admin_create_trust(
            body=ICreateTrust(name="GSTT"),
            db=MagicMock(),
            token_id=admin_id,
        )
    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN


@patch("flip_api.trusts_services.admin_create_trust.has_permissions", return_value=True)
def test_admin_create_trust_409_on_duplicate_name(_mock_perms, admin_id):  # noqa: PT019  -- patch-injected, not a fixture
    existing = Trust(id=uuid4(), name="GSTT")
    db = _mock_db(slot=FLKitSlot(slot_name="x", slot_number=0), existing_trust=existing)

    with pytest.raises(HTTPException) as exc_info:
        admin_create_trust(
            body=ICreateTrust(name="GSTT"),
            db=db,
            token_id=admin_id,
        )

    assert exc_info.value.status_code == status.HTTP_409_CONFLICT
    assert "already exists" in exc_info.value.detail
