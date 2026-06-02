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

"""Thin HTTP wrapper tests — service body is covered in ``test_register_trust``."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException, status
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from flip_api.db.models.main_models import FLKitSlot, Trust
from flip_api.domain.interfaces.trust import ICreateTrust
from flip_api.trusts_services.admin_create_trust import admin_create_trust
from flip_api.trusts_services.services.register_trust import (
    DuplicateTrustError,
    EmptyTrustCodeError,
    EmptyTrustNameError,
    NoFreeKitSlotError,
    RegisteredTrust,
)


@pytest.fixture
def admin_id() -> UUID:
    return uuid4()


def _registered(name: str = "GSTT", slot_name: str = "Trust_007", slot_number: int = 7) -> RegisteredTrust:
    return RegisteredTrust(
        trust=Trust(
            id=uuid4(),
            name=name,
            code="GSTT",
            region="London",
            api_key_hash="hash-api",
            created_at=datetime.now(timezone.utc),
        ),
        fl_kit_slot=FLKitSlot(slot_name=slot_name, slot_number=slot_number),
        trust_api_key="plain-api",
        trust_internal_service_key="plain-internal",
    )


@patch("flip_api.trusts_services.admin_create_trust.has_permissions", return_value=True)
@patch("flip_api.trusts_services.admin_create_trust.register_trust")
def test_admin_create_trust_returns_registered_kit(mock_register, mock_perms, admin_id):
    mock_register.return_value = _registered()
    db = MagicMock()

    result = admin_create_trust(
        body=ICreateTrust(name="GSTT", code="GSTT", region="London"),
        db=db,
        token_id=admin_id,
    )

    mock_register.assert_called_once_with(
        name="GSTT", code="GSTT", region="London", session=db, audit_user_id=admin_id
    )
    assert result.name == "GSTT"
    assert result.fl_kit_slot == "Trust_007"
    assert result.fl_kit_slot_number == 7
    assert result.trust_api_key == "plain-api"
    assert result.trust_internal_service_key == "plain-internal"
    db.rollback.assert_not_called()
    mock_perms.assert_called_once()


@patch("flip_api.trusts_services.admin_create_trust.has_permissions", return_value=False)
def test_admin_create_trust_403_without_admin_permission(mock_perms, admin_id):
    with pytest.raises(HTTPException) as exc_info:
        admin_create_trust(body=ICreateTrust(name="GSTT", code="GSTT"), db=MagicMock(), token_id=admin_id)
    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    mock_perms.assert_called_once()


@patch("flip_api.trusts_services.admin_create_trust.has_permissions", return_value=True)
@patch("flip_api.trusts_services.admin_create_trust.register_trust")
def test_admin_create_trust_400_on_empty_name(mock_register, mock_perms, admin_id):
    mock_register.side_effect = EmptyTrustNameError("Trust name is required.")
    db = MagicMock()

    with pytest.raises(HTTPException) as exc_info:
        admin_create_trust(body=ICreateTrust(name="GSTT", code="GSTT"), db=db, token_id=admin_id)

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    db.rollback.assert_called_once()


@patch("flip_api.trusts_services.admin_create_trust.has_permissions", return_value=True)
@patch("flip_api.trusts_services.admin_create_trust.register_trust")
def test_admin_create_trust_400_on_empty_code(mock_register, mock_perms, admin_id):
    # A whitespace-only code passes the schema's min_length but the service strips
    # it to empty and rejects it — the wrapper maps that to 400, like an empty name.
    mock_register.side_effect = EmptyTrustCodeError("Trust code is required.")
    db = MagicMock()

    with pytest.raises(HTTPException) as exc_info:
        admin_create_trust(body=ICreateTrust(name="GSTT", code=" "), db=db, token_id=admin_id)

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    db.rollback.assert_called_once()


def test_create_trust_schema_requires_code():
    # The request body must carry a non-empty code — FastAPI returns 422 for these
    # before the handler runs, so the UI cannot register a trust without a code.
    with pytest.raises(ValidationError):
        ICreateTrust(name="GSTT")
    with pytest.raises(ValidationError):
        ICreateTrust(name="GSTT", code="")


@patch("flip_api.trusts_services.admin_create_trust.has_permissions", return_value=True)
@patch("flip_api.trusts_services.admin_create_trust.register_trust")
def test_admin_create_trust_409_on_duplicate(mock_register, mock_perms, admin_id):
    mock_register.side_effect = DuplicateTrustError("A trust named 'GSTT' already exists.")
    db = MagicMock()

    with pytest.raises(HTTPException) as exc_info:
        admin_create_trust(body=ICreateTrust(name="GSTT", code="GSTT"), db=db, token_id=admin_id)

    assert exc_info.value.status_code == status.HTTP_409_CONFLICT
    assert "already exists" in exc_info.value.detail
    db.rollback.assert_called_once()


@patch("flip_api.trusts_services.admin_create_trust.has_permissions", return_value=True)
@patch("flip_api.trusts_services.admin_create_trust.register_trust")
def test_admin_create_trust_409_when_pool_exhausted(mock_register, mock_perms, admin_id):
    mock_register.side_effect = NoFreeKitSlotError("No FL kit slots available.")
    db = MagicMock()

    with pytest.raises(HTTPException) as exc_info:
        admin_create_trust(body=ICreateTrust(name="GSTT", code="GSTT"), db=db, token_id=admin_id)

    assert exc_info.value.status_code == status.HTTP_409_CONFLICT
    assert "FL kit slots" in exc_info.value.detail
    db.rollback.assert_called_once()


@patch("flip_api.trusts_services.admin_create_trust.has_permissions", return_value=True)
@patch("flip_api.trusts_services.admin_create_trust.register_trust")
def test_admin_create_trust_500_on_db_error(mock_register, mock_perms, admin_id):
    mock_register.side_effect = SQLAlchemyError("db boom")
    db = MagicMock()

    with pytest.raises(HTTPException) as exc_info:
        admin_create_trust(body=ICreateTrust(name="GSTT", code="GSTT"), db=db, token_id=admin_id)

    assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    db.rollback.assert_called_once()
