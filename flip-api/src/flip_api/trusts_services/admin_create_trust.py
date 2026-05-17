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

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, col, select

from flip_api.auth.auth_utils import has_permissions
from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.db.models.main_models import FLKitSlot, Trust
from flip_api.db.models.user_models import PermissionRef
from flip_api.domain.interfaces.trust import ICreatedTrust, ICreateTrust
from flip_api.scripts.generate_trust_key import generate_trust_key
from flip_api.utils.logger import logger

router = APIRouter(prefix="/admin/trusts", tags=["trusts_services"])


@router.post("", response_model=ICreatedTrust, status_code=status.HTTP_201_CREATED)
def admin_create_trust(
    body: ICreateTrust,
    db: Session = Depends(get_session),
    token_id: UUID = Depends(verify_token),
) -> ICreatedTrust:
    """Create a trust and return its newly-generated plaintext keys (returned once).

    Only the SHA-256 of the trust API key is persisted. The trust internal service
    key is returned to the caller but not stored on the hub — it only lives in the
    trust-side environment and protects trust-internal services (imaging-api,
    data-access-api).

    Args:
        body (ICreateTrust): Request body with the trust name.
        db (Session): Database session.
        token_id (UUID): Authenticated user ID, used for the admin permission check.

    Returns:
        ICreatedTrust: The new trust plus plaintext ``trust_api_key`` and
        ``trust_internal_service_key`` — surface to the admin once, then discard.

    Raises:
        HTTPException: 403 if the caller lacks ``CAN_ACCESS_ADMIN_PANEL``;
            400 if the name is empty; 409 if a trust with the given name exists;
            500 on database error.
    """
    if not has_permissions(token_id, [PermissionRef.CAN_ACCESS_ADMIN_PANEL], db):
        logger.error(f"User {token_id} attempted to create a trust without admin permission")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin permission required to create trusts.",
        )

    name = body.name.strip()
    if not name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Trust name is required.",
        )

    code = body.code.strip() if body.code else None
    region = body.region.strip() if body.region else None

    existing = db.exec(select(Trust).where(Trust.name == name)).first()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A trust named {name!r} already exists.",
        )

    api_key, api_key_hash = generate_trust_key()
    internal_key, _internal_hash = generate_trust_key()

    trust = Trust(
        name=name,
        code=code,
        region=region,
        api_key_hash=api_key_hash,
        created_at=datetime.now(timezone.utc),
    )

    # Pick the next free FL kit slot and bind it to this trust in the same transaction
    # as the insert — a 409 here means the operator can't bring the trust up anyway
    # (no kit to mount), so failing pre-insert is the right behaviour. SKIP LOCKED so
    # two concurrent admin creates don't collide on the same slot.
    slot_stmt = (
        select(FLKitSlot)
        .where(col(FLKitSlot.assigned_to_trust_id).is_(None))
        .order_by(col(FLKitSlot.slot_number).asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )

    try:
        slot = db.exec(slot_stmt).first()
        if slot is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "No FL kit slots available. Pre-provision more slots in flip-fl-base "
                    "and add them to FL_KIT_SLOT_NAMES."
                ),
            )

        db.add(trust)
        db.flush()  # populate trust.id without committing yet
        slot.assigned_to_trust_id = trust.id
        slot.assigned_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(trust)
        db.refresh(slot)
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError as e:
        db.rollback()
        logger.exception("Error creating trust")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from e

    logger.info(
        f"Admin {token_id} created trust {trust.id} ({trust.name}); assigned FL kit slot {slot.slot_name}"
    )

    return ICreatedTrust(
        id=trust.id,
        name=trust.name,
        code=trust.code,
        region=trust.region,
        created_at=trust.created_at,
        trust_api_key=api_key,
        trust_internal_service_key=internal_key,
        fl_kit_slot=slot.slot_name,
        fl_kit_slot_number=slot.slot_number,
    )
