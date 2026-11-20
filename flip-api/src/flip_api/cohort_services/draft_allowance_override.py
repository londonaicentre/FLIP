# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
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

"""Admin override of one user's cohort drafting limits (FLIP#1556), kept apart from the drafting routes.

Only a holder of CAN_MANAGE_USERS may read, set or clear an override, and every set or clear writes a
``cohort_draft_override_audit`` row (actor, target, old and new values, reason, time) in the same
transaction as the change.
"""

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from flip_api.auth.auth_utils import has_permissions
from flip_api.auth.dependencies import verify_token
from flip_api.cohort_services.drafting.ledger import utcnow
from flip_api.db.database import get_session
from flip_api.db.models.main_models import CohortDraftOverride, CohortDraftOverrideAudit
from flip_api.db.models.user_models import PermissionRef, UserProfile, UserRole
from flip_api.domain.schemas.cohort_draft import (
    CohortDraftOverrideBody,
    CohortDraftOverrideClearBody,
    CohortDraftOverrideView,
)
from flip_api.utils.logger import logger

router = APIRouter(prefix="/cohort/draft/overrides", tags=["cohort_services"])


def _require_user_manager(db: Session, caller: UUID) -> None:
    if not has_permissions(caller, [PermissionRef.CAN_MANAGE_USERS], db):
        logger.warning(f"User with ID: {caller} attempted to manage a cohort drafting override without permission")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You are not allowed to manage users")


def _require_known_user(db: Session, user_id: UUID) -> None:
    if (
        db.get(UserProfile, user_id) is None
        and db.exec(select(UserRole.id).where(UserRole.user_id == user_id)).first() is None
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")


def _view(override: CohortDraftOverride, now: datetime) -> CohortDraftOverrideView:
    return CohortDraftOverrideView(
        user_id=override.user_id,
        monthly_allowance=override.monthly_allowance,
        monthly_request_cap=override.monthly_request_cap,
        expires_at=override.expires_at.replace(tzinfo=timezone.utc) if override.expires_at else None,
        active=override.expires_at is None or override.expires_at > now,
        updated_by=override.updated_by,
        updated_at=override.updated_at.replace(tzinfo=timezone.utc),
    )


def _audit(
    db: Session,
    actor: UUID,
    target: UUID,
    action: str,
    reason: str,
    now: datetime,
    old: CohortDraftOverride | None,
    new: CohortDraftOverride | None,
) -> None:
    db.add(
        CohortDraftOverrideAudit(
            actor_user_id=actor,
            target_user_id=target,
            action=action,
            old_monthly_allowance=old.monthly_allowance if old else None,
            new_monthly_allowance=new.monthly_allowance if new else None,
            old_monthly_request_cap=old.monthly_request_cap if old else None,
            new_monthly_request_cap=new.monthly_request_cap if new else None,
            old_expires_at=old.expires_at if old else None,
            new_expires_at=new.expires_at if new else None,
            reason=reason,
            timestamp=now,
        )
    )


@router.get(
    "/{user_id}",
    response_model=CohortDraftOverrideView,
    summary="Read a user's cohort drafting override (admin)",
)
def get_cohort_draft_override(
    user_id: UUID, db: Session = Depends(get_session), caller: UUID = Depends(verify_token)
) -> CohortDraftOverrideView:
    """Return the user's override, expired or not.

    Raises:
        HTTPException: 403 without CAN_MANAGE_USERS, 404 when the user has no override.
    """
    _require_user_manager(db, caller)
    override = db.get(CohortDraftOverride, user_id)
    if override is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No override for this user")
    return _view(override, utcnow())


@router.put(
    "/{user_id}",
    response_model=CohortDraftOverrideView,
    summary="Set a user's cohort drafting limits (admin)",
    description="Replaces any override for the user. Audited, with the reason given.",
)
def set_cohort_draft_override(
    user_id: UUID,
    body: CohortDraftOverrideBody,
    db: Session = Depends(get_session),
    caller: UUID = Depends(verify_token),
) -> CohortDraftOverrideView:
    """Set the user's monthly allowance (and optionally request cap) until an optional expiry.

    Raises:
        HTTPException: 403 without CAN_MANAGE_USERS, 404 for an unknown user, 400 for a past expiry.
    """
    _require_user_manager(db, caller)
    _require_known_user(db, user_id)
    now = utcnow()
    expires_at = None
    if body.expires_at is not None:
        aware = body.expires_at if body.expires_at.tzinfo else body.expires_at.replace(tzinfo=timezone.utc)
        expires_at = aware.astimezone(timezone.utc).replace(tzinfo=None)
        if expires_at <= now:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="expires_at must be in the future")

    override = db.exec(
        select(CohortDraftOverride).where(CohortDraftOverride.user_id == user_id).with_for_update()
    ).first()
    old = CohortDraftOverride.model_validate(override.model_dump()) if override else None
    if override is None:
        override = CohortDraftOverride(user_id=user_id, monthly_allowance=0, updated_by=caller, updated_at=now)
    override.monthly_allowance = body.monthly_allowance
    override.monthly_request_cap = body.monthly_request_cap
    override.expires_at = expires_at
    override.updated_by, override.updated_at = caller, now
    db.add(override)
    _audit(db, caller, user_id, "set", body.reason, now, old, override)
    db.commit()
    db.refresh(override)
    logger.info(f"User {caller} set a cohort drafting override for user {user_id}")
    return _view(override, now)


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Clear a user's cohort drafting override (admin)",
    description="The user returns to the configured limits. Audited, with the reason given.",
)
def clear_cohort_draft_override(
    user_id: UUID,
    body: CohortDraftOverrideClearBody,
    db: Session = Depends(get_session),
    caller: UUID = Depends(verify_token),
) -> None:
    """Remove the user's override.

    Raises:
        HTTPException: 403 without CAN_MANAGE_USERS, 404 when the user has no override.
    """
    _require_user_manager(db, caller)
    override = db.exec(
        select(CohortDraftOverride).where(CohortDraftOverride.user_id == user_id).with_for_update()
    ).first()
    if override is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No override for this user")
    now = utcnow()
    _audit(db, caller, user_id, "clear", body.reason, now, override, None)
    db.delete(override)
    db.commit()
    logger.info(f"User {caller} cleared the cohort drafting override for user {user_id}")
