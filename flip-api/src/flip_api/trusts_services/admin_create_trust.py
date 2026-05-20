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

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

from flip_api.auth.auth_utils import has_permissions
from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.db.models.user_models import PermissionRef
from flip_api.domain.interfaces.trust import ICreatedTrust, ICreateTrust
from flip_api.trusts_services.services.register_trust import (
    DuplicateTrustError,
    EmptyTrustNameError,
    NoFreeKitSlotError,
    register_trust,
)
from flip_api.utils.logger import logger

router = APIRouter(prefix="/admin/trusts", tags=["trusts_services"])


@router.post("", response_model=ICreatedTrust, status_code=status.HTTP_201_CREATED)
def admin_create_trust(
    body: ICreateTrust,
    db: Session = Depends(get_session),
    token_id: UUID = Depends(verify_token),
) -> ICreatedTrust:
    """Create a trust and return its newly-generated plaintext keys (returned once).

    Thin HTTP wrapper over ``register_trust`` — translates service errors to
    400 / 409 / 500 responses. The hub stores only the SHA-256 hash of the trust
    API key. The trust internal service key is returned to the caller but not
    stored on the hub — it only lives in the trust-side environment and protects
    trust-internal services (imaging-api, data-access-api).

    Args:
        body (ICreateTrust): Request body — trust name plus optional code / region.
        db (Session): Database session.
        token_id (UUID): Authenticated user ID, used for the admin permission check.

    Returns:
        ICreatedTrust: The new trust plus plaintext ``trust_api_key`` and
        ``trust_internal_service_key`` — surface to the admin once, then discard.

    Raises:
        HTTPException: 403 if the caller lacks ``CAN_ACCESS_ADMIN_PANEL``;
            400 if the name is empty; 409 if a trust with the given name exists
            or no FL kit slot is available; 500 on database error.
    """
    if not has_permissions(token_id, [PermissionRef.CAN_ACCESS_ADMIN_PANEL], db):
        logger.error(f"User {token_id} attempted to create a trust without admin permission")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin permission required to create trusts.",
        )

    try:
        registered = register_trust(
            name=body.name,
            code=body.code,
            region=body.region,
            session=db,
        )
    except EmptyTrustNameError as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except (DuplicateTrustError, NoFreeKitSlotError) as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except SQLAlchemyError as e:
        db.rollback()
        logger.exception("Error creating trust")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from e

    logger.info(
        f"Admin {token_id} created trust {registered.trust.id} ({registered.trust.name}); "
        f"assigned FL kit slot {registered.fl_kit_slot.slot_name}"
    )

    return ICreatedTrust(
        id=registered.trust.id,
        name=registered.trust.name,
        code=registered.trust.code,
        region=registered.trust.region,
        created_at=registered.trust.created_at,
        trust_api_key=registered.trust_api_key,
        trust_internal_service_key=registered.trust_internal_service_key,
        fl_kit_slot=registered.fl_kit_slot.slot_name,
        fl_kit_slot_number=registered.fl_kit_slot.slot_number,
    )
