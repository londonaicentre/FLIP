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
"""Download a trust's kit from the UI (FLIP#1852).

Open to the trust's own Trust Admin and to hub admins. The kit and its passphrase leave in one
response and are kept nowhere on the hub; what stays is a ``trusts_audit`` row per download.
"""

import base64
import hmac
from datetime import timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Response, status
from sqlmodel import Session, col, func, select

from flip_api.auth.auth_utils import has_permissions, has_trust_permissions
from flip_api.auth.dependencies import verify_token
from flip_api.config import get_settings
from flip_api.db.database import get_session
from flip_api.db.models.main_models import FLKitSlot, Trust, TrustsAudit
from flip_api.db.models.user_models import PermissionRef, UserProfile
from flip_api.domain.interfaces.trust import IRotateTrustKit, ITrustKitDownload, ITrustKitLastDownload, ITrustKitStatus
from flip_api.domain.schemas.actions import TrustAuditAction
from flip_api.trusts_services.services.register_trust import RegisteredTrust, issue_trust_keys
from flip_api.trusts_services.services.trust_kit import (
    BuiltKit,
    KitParts,
    TrustKitError,
    build_kit,
    hub_shared_digest,
    hub_shared_values,
    load_kit_parts,
)
from flip_api.trusts_services.utils.audit_helper import audit_trust_action
from flip_api.utils.logger import logger

router = APIRouter(prefix="/trust", tags=["trusts_services"])


def _authorised_trust(trust_id: UUID, user_id: UUID, db: Session, lock: bool = False) -> Trust:
    """The trust, if the caller is its Trust Admin or a hub admin.

    Raises:
        HTTPException: 404 if the trust does not exist; 403 for any other caller.
    """
    query = select(Trust).where(Trust.id == trust_id)
    # Two first downloads must not both mint keys: the second waits here and finds them issued.
    trust = db.exec(query.with_for_update() if lock else query).first()
    if trust is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Trust {trust_id} not found")
    if not (
        has_trust_permissions(user_id, [PermissionRef.CAN_MANAGE_TRUST_KIT], trust_id, db)
        or has_permissions(user_id, [PermissionRef.CAN_ACCESS_ADMIN_PANEL], db)
    ):
        logger.error(f"User {user_id} may not manage the kit of trust {trust_id}")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You may not manage this trust's kit.")
    return trust


def _kit_parts(trust: Trust, db: Session) -> tuple[FLKitSlot, KitParts]:
    """The trust's slot and everything in its kit but the keys, or a 409 saying what is missing."""
    slot = db.exec(select(FLKitSlot).where(FLKitSlot.assigned_to_trust_id == trust.id)).first()
    try:
        if slot is None:
            raise TrustKitError("This trust has no FL kit slot assigned.")
        return slot, load_kit_parts(trust, slot)
    except TrustKitError as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e


def _issue(
    trust: Trust, slot: FLKitSlot, parts: KitParts, user_id: UUID, db: Session, keys: TrustAuditAction | None
) -> ITrustKitDownload:
    """Record the download, mint keys when ``keys`` names why, and return the encrypted kit.

    The download row is written first so that it commits with the key change: keys that were minted
    but never recorded as handed over could not be told from keys nobody received.
    """
    kind = "full" if keys else "update"
    audit_trust_action(
        trust_id=trust.id,
        trust_name=trust.name,
        action=TrustAuditAction.KIT_DOWNLOADED,
        user_id=user_id,
        session=db,
        detail={"kind": kind, "fl_backend": parts.backend.value, "digest": hub_shared_digest(parts.shared)},
    )
    if keys:
        registered = issue_trust_keys(db, trust=trust, slot=slot, action=keys, user_id=user_id)
    else:
        db.commit()
        registered = RegisteredTrust(trust=trust, fl_kit_slot=slot, trust_api_key=None, trust_internal_service_key=None)

    kit: BuiltKit = build_kit(registered, parts)
    logger.info(f"User {user_id} downloaded the {kind} kit of trust {trust.id} ({trust.name})")
    return ITrustKitDownload(
        file_name=kit.file_name,
        kind=kit.kind,
        passphrase=kit.passphrase,
        content_base64=base64.b64encode(kit.content).decode(),
    )


@router.get(
    "/{trust_id}/kit",
    summary="Whether a trust's kit has been downloaded, by whom, and whether it is out of date.",
    response_model=ITrustKitStatus,
)
def get_trust_kit_status(
    trust_id: UUID = Path(..., description="The trust."),
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
) -> ITrustKitStatus:
    """The state of a trust's kit, for its card.

    Args:
        trust_id (UUID): The trust.
        db (Session): Database session.
        user_id (UUID): The caller.

    Returns:
        ITrustKitStatus: Whether keys are issued, the last download and whether a newer kit exists.

    Raises:
        HTTPException: 404 if the trust does not exist; 403 unless the caller is its Trust Admin or a hub admin.
    """
    trust = _authorised_trust(trust_id, user_id, db)
    slot = db.exec(select(FLKitSlot).where(FLKitSlot.assigned_to_trust_id == trust.id)).first()

    downloads = (TrustsAudit.trust_id == trust.id, TrustsAudit.action == TrustAuditAction.KIT_DOWNLOADED)
    count = db.exec(select(func.count()).select_from(TrustsAudit).where(*downloads)).one()
    last = db.exec(
        select(TrustsAudit, UserProfile.name)
        .join(UserProfile, col(UserProfile.user_id) == col(TrustsAudit.modified_by_user_id), isouter=True)
        .where(*downloads)
        .order_by(col(TrustsAudit.audit_date).desc())
        .limit(1)
    ).first()

    update_available = False
    last_download = None
    if last is not None:
        row, name = last
        detail = row.detail or {}
        # audit_date is stored as naive UTC; say so, or a browser reads it as local time.
        at = row.audit_date.replace(tzinfo=timezone.utc)
        last_download = ITrustKitLastDownload(at=at, kind=detail.get("kind", "update"), by=name or None)
        try:
            update_available = detail.get("digest") != hub_shared_digest(hub_shared_values())
        except TrustKitError:
            # A hub that cannot issue a kit has no newer one to offer; the download itself says why.
            pass

    return ITrustKitStatus(
        keys_issued=trust.api_key_hash is not None,
        fl_backend=get_settings().FL_BACKEND,
        fl_kit_slot=slot.slot_name if slot else None,
        download_count=count,
        last_download=last_download,
        update_available=update_available,
    )


@router.post(
    "/{trust_id}/kit",
    summary="Download a trust's kit. The first download mints the trust's keys.",
    response_model=ITrustKitDownload,
)
def download_trust_kit(
    response: Response,
    trust_id: UUID = Path(..., description="The trust."),
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
) -> ITrustKitDownload:
    """Build the trust's kit and return it encrypted, with its passphrase.

    A trust whose keys were never issued gets them minted here and a full kit; every later call
    returns an update kit and leaves the keys alone.

    Args:
        response (Response): The response, marked uncacheable.
        trust_id (UUID): The trust.
        db (Session): Database session.
        user_id (UUID): The caller.

    Returns:
        ITrustKitDownload: The encrypted kit and the one copy of its passphrase.

    Raises:
        HTTPException: 404 if the trust does not exist; 403 unless the caller is its Trust Admin or a
            hub admin; 409 if the hub cannot build the kit.
    """
    response.headers["Cache-Control"] = "no-store"
    trust = _authorised_trust(trust_id, user_id, db, lock=True)
    slot, parts = _kit_parts(trust, db)
    first = trust.api_key_hash is None
    return _issue(trust, slot, parts, user_id, db, keys=TrustAuditAction.KEYS_ISSUED if first else None)


@router.post(
    "/{trust_id}/kit/rotate",
    summary="Issue a trust new keys and download its kit. The installed kit stops working.",
    response_model=ITrustKitDownload,
)
def rotate_trust_kit(
    body: IRotateTrustKit,
    response: Response,
    trust_id: UUID = Path(..., description="The trust."),
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
) -> ITrustKitDownload:
    """Reissue the trust's keys and return a full kit.

    The hub refuses the old api key from the moment this commits, so the trust is offline until the
    new kit is installed.

    Args:
        body (IRotateTrustKit): The trust's code, typed back by the caller.
        response (Response): The response, marked uncacheable.
        trust_id (UUID): The trust.
        db (Session): Database session.
        user_id (UUID): The caller.

    Returns:
        ITrustKitDownload: The encrypted kit and the one copy of its passphrase.

    Raises:
        HTTPException: 404 if the trust does not exist; 403 unless the caller is its Trust Admin or a
            hub admin; 400 if the code does not match; 409 if the hub cannot build the kit.
    """
    response.headers["Cache-Control"] = "no-store"
    trust = _authorised_trust(trust_id, user_id, db, lock=True)
    if not trust.code or not hmac.compare_digest(body.confirm_code.encode(), trust.code.encode()):
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The trust code does not match.")
    slot, parts = _kit_parts(trust, db)
    action = TrustAuditAction.ROTATED if trust.api_key_hash else TrustAuditAction.KEYS_ISSUED
    return _issue(trust, slot, parts, user_id, db, keys=action)
