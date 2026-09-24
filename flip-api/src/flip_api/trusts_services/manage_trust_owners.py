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

"""Who holds authority at a trust (FLIP#1258).

Approval is a site decision, so the site needs a way to say *who* speaks for it — and a
hub operator needs a way to hand that authority over and to recover it if a trust ends up
with nobody. This is the endpoint pair that makes the per-trust grant usable in practice:
without it, granting ``TRUST_OWNER`` requires editing the database, which is not a
workflow a site can operate.

Authorization follows the same rule as every other trust-scoped action: authority is held
**at** a trust, and must be asked about that trust. A Trust Owner of trust A cannot
nominate an owner for trust B, and a platform-wide Admin grant confers nothing here —
that is what keeps "hub administration" and "trust authority" separate.

A caveat accepted deliberately: because a grant holder can also revoke one, an admin who
holds Trust Owner at a trust can restore themselves after an operator removes them. The
remedy is a platform-level action, not an API rule — see the note on ``remove_owner``.
"""

from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Path, status
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, col, select

from flip_api.auth.auth_utils import has_permissions, has_trust_permissions
from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.db.models.main_models import Trust
from flip_api.db.models.user_models import PermissionRef, Role, UserProfile, UserRole
from flip_api.domain.schemas.actions import TrustAuditAction
from flip_api.domain.schemas.trusts import AddTrustOwner, TrustOwner
from flip_api.trusts_services.utils.audit_helper import audit_trust_action
from flip_api.utils.logger import logger

router = APIRouter(prefix="/admin/trusts", tags=["trusts_services"])

# Resolved once. The seeder guarantees both roles exist; a missing one is a broken install,
# not a caller error, so the lookups below 500 rather than guessing.
_TRUST_OWNER = "Trust Owner"


def _trust_owner_role_id(db: Session) -> UUID:
    """The ``Trust Owner`` role's id, or 500 — every operation here needs it."""
    role_id = db.exec(select(col(Role.id)).where(col(Role.name) == _TRUST_OWNER)).first()
    if role_id is None:
        logger.error(f"The '{_TRUST_OWNER}' role is missing; the database is not correctly seeded")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        )
    return role_id


def _require_trust(db: Session, trust_id: UUID) -> Trust:
    """The trust, or 404 — checked before any permission question about it.

    A caller able to manage owners at one trust must not learn which trust ids exist
    elsewhere in the federation, and a 404 for a trust they do hold authority over is not
    reachable: the permission check needs the row to have passed this gate first.
    """
    trust = db.get(Trust, trust_id)
    if trust is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Trust with ID: {trust_id} does not exist",
        )
    return trust


def _owner_count(db: Session, role_id: UUID, trust_id: UUID, exclude_grant_id: UUID | None = None) -> int:
    """How many owners the trust has, optionally ignoring one grant.

    ``exclude_grant_id`` answers "how many would remain if this grant went away", which is
    the question the last-owner rule needs and a plain count cannot express.
    """
    statement = select(UserRole).where(col(UserRole.role_id) == role_id).where(col(UserRole.trust_id) == trust_id)
    if exclude_grant_id is not None:
        statement = statement.where(col(UserRole.id) != exclude_grant_id)
    return len(db.exec(statement).all())


def _refuse_owner_change(user_id: UUID, trust_id: UUID, reason: str) -> None:
    """The single refusal for owner management, so both paths answer identically."""
    logger.error(f"User {user_id} may not manage owners for trust {trust_id}: {reason}")
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Permission required to manage this trust's owners",
    )


def _require_owner_manager(user_id: UUID, trust_id: UUID, db: Session) -> None:
    """Refuse unless the caller may manage owners at THIS trust.

    ``has_trust_permissions`` ignores global grants by construction, so a hub Admin — who
    holds every *global* permission but deliberately none of the trust-scoped ones — is
    refused. If that ever regressed, one hub administrator could nominate themselves owner
    of any trust and then approve its projects, which is the hole FLIP#1258 exists to close.
    """
    if not has_trust_permissions(user_id, [PermissionRef.CAN_MANAGE_TRUST_OWNERS], trust_id, db):
        _refuse_owner_change(
            user_id,
            trust_id,
            "CAN_MANAGE_TRUST_OWNERS is required at that trust, and global grants do not satisfy it",
        )


def _authorize_grant(user_id: UUID, trust_id: UUID, role_id: UUID, db: Session) -> None:
    """Authorise adding an owner, allowing the bootstrap case a new trust depends on.

    The trust-scoped rule alone cannot start a trust off: a newly registered trust has no
    ``user_role`` rows, so nobody holds ``CAN_MANAGE_TRUST_OWNERS`` at it and nobody could
    ever appoint its first owner — the trust would be permanently unable to approve a
    project. Registration is gated by the hub-wide ``CAN_ACCESS_ADMIN_PANEL``, so the same
    authority bootstraps the first grant here.

    Deliberately narrower than the permission it replaced: it applies **only while the trust
    has no owners at all**, so it cannot appoint an owner at a trust that already has one —
    which is every existing trust after the continuity migration — and it confers no ability
    to approve. The last-owner guard elsewhere stops an admin manufacturing the condition by
    emptying a trust first.
    """
    if has_trust_permissions(user_id, [PermissionRef.CAN_MANAGE_TRUST_OWNERS], trust_id, db):
        return
    if _owner_count(db, role_id, trust_id) == 0 and has_permissions(
        user_id, [PermissionRef.CAN_ACCESS_ADMIN_PANEL], db
    ):
        logger.warning(
            f"Bootstrapping the first Trust Owner of trust {trust_id} as {user_id} via "
            f"CAN_ACCESS_ADMIN_PANEL: the trust had no owner, so no one held "
            f"CAN_MANAGE_TRUST_OWNERS at it"
        )
        return
    _refuse_owner_change(
        user_id,
        trust_id,
        "CAN_MANAGE_TRUST_OWNERS is required at that trust, and global grants do not satisfy it "
        "except to bootstrap a trust that has no owner at all",
    )


@router.get("/{trust_id}/owners", response_model=list[TrustOwner])
def list_trust_owners(
    trust_id: UUID = Path(..., description="ID of the trust"),
    db: Session = Depends(get_session),
    token_id: UUID = Depends(verify_token),
) -> list[TrustOwner]:
    """List the users who hold authority at a trust.

    Readable by any authenticated user: knowing which organisation's staff speak for a
    named trust is operational information a researcher needs (it is how they know who to
    contact about a stalled project), and it is the same class of benign directory data the
    trust list itself already exposes. No secret is disclosed — a user id and their own
    profile's name/organisation, nothing about permissions held.

    Args:
        trust_id (UUID): ID of the trust.
        db (Session): Database session.
        token_id (UUID): Authenticated user ID.

    Returns:
        list[TrustOwner]: Current owners, ordered by name for a stable UI list.

    Raises:
        HTTPException: 404 if the trust does not exist.
    """
    _require_trust(db, trust_id)
    role_id = _trust_owner_role_id(db)

    rows = db.exec(
        select(UserProfile)
        .join(UserRole, col(UserProfile.user_id) == col(UserRole.user_id))
        .where(col(UserRole.role_id) == role_id)
        .where(col(UserRole.trust_id) == trust_id)
        .order_by(col(UserProfile.name))
    ).all()
    # A grant whose holder has no profile row still has to appear: the grant is what confers
    # authority, and hiding it would misrepresent who can act for this trust.
    listed = {profile.user_id for profile in rows}
    granted = db.exec(
        select(col(UserRole.user_id)).where(col(UserRole.role_id) == role_id).where(col(UserRole.trust_id) == trust_id)
    ).all()

    owners = [TrustOwner(user_id=p.user_id, name=p.name, organisation=p.organisation) for p in rows]
    owners.extend(TrustOwner(user_id=uid) for uid in granted if uid not in listed)
    return sorted(owners, key=lambda owner: owner.name)


@router.post("/{trust_id}/owners", response_model=TrustOwner, status_code=status.HTTP_201_CREATED)
def add_trust_owner(
    trust_id: UUID = Path(..., description="ID of the trust"),
    body: AddTrustOwner = Body(...),
    db: Session = Depends(get_session),
    token_id: UUID = Depends(verify_token),
) -> TrustOwner:
    """Make a user a Trust Owner of a trust.

    Idempotent in effect: an existing grant is returned rather than duplicated, so a UI
    retry or a double-click cannot create a second row (which the partial unique index
    would reject anyway — this turns that into a 201 with the current owner instead of a
    409 the caller cannot act on).

    Args:
        trust_id (UUID): ID of the trust.
        body (AddTrustOwner): The user to nominate.
        db (Session): Database session.
        token_id (UUID): Authenticated user ID, used for the trust-scoped permission check.

    Returns:
        TrustOwner: The nominated owner.

    Raises:
        HTTPException: 404 if the trust does not exist; 403 if the caller may not manage
            this trust's owners; 500 on database error.
    """
    trust = _require_trust(db, trust_id)
    role_id = _trust_owner_role_id(db)
    _authorize_grant(token_id, trust_id, role_id, db)

    already = db.exec(
        select(UserRole)
        .where(col(UserRole.user_id) == body.user_id)
        .where(col(UserRole.role_id) == role_id)
        .where(col(UserRole.trust_id) == trust_id)
    ).first()

    if already is None:
        try:
            db.add(UserRole(user_id=body.user_id, role_id=role_id, trust_id=trust_id))
            audit_trust_action(
                trust_id=trust_id,
                trust_name=trust.name,
                action=TrustAuditAction.OWNER_ADDED,
                user_id=token_id,
                session=db,
            )
            # One commit: the grant and its audit row land together, so an audit trail
            # never claims a change that a later failure rolled back.
            db.commit()
        except IntegrityError:
            # Lost a race with a concurrent grant of the same triple. Not an error the
            # caller can act on — the desired end state holds — so report success.
            db.rollback()
            logger.info(f"Concurrent grant of Trust Owner to {body.user_id} at trust {trust_id}; already present")
        except SQLAlchemyError as e:
            db.rollback()
            logger.exception(f"Error granting Trust Owner to {body.user_id} at trust {trust_id}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Internal server error",
            ) from e
        logger.info(f"User {token_id} made {body.user_id} a Trust Owner of trust {trust_id}")

    profile = db.exec(select(UserProfile).where(UserProfile.user_id == body.user_id)).first()
    return TrustOwner(
        user_id=body.user_id,
        name=profile.name if profile else "",
        organisation=profile.organisation if profile else "",
    )


@router.delete("/{trust_id}/owners/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_trust_owner(
    trust_id: UUID = Path(..., description="ID of the trust"),
    user_id: UUID = Path(..., description="Cognito sub of the owner to remove"),
    db: Session = Depends(get_session),
    token_id: UUID = Depends(verify_token),
) -> None:
    """Remove a user's Trust Owner authority at a trust.

    Refused when it would leave the trust with no owner. A trust nobody speaks for cannot
    approve projects, nominate its replacement, or fix its governance policy — it is
    stranded until a platform operator intervenes, and the failure is silent until someone
    tries to do something. Recovering from an over-broad grant is a two-step operation
    (nominate the successor, then remove the incumbent) rather than a lockout.

    Self-removal is allowed: an admin stepping back per trust is the handover this endpoint
    exists for, and the last-owner rule above is what stops it becoming a lockout.

    Args:
        trust_id (UUID): ID of the trust.
        user_id (UUID): The owner to remove.
        db (Session): Database session.
        token_id (UUID): Authenticated user ID, used for the trust-scoped permission check.

    Raises:
        HTTPException: 404 if the trust or the grant does not exist; 403 if the caller may
            not manage this trust's owners; 409 if this is the trust's last owner; 500 on
            database error.
    """
    trust = _require_trust(db, trust_id)
    _require_owner_manager(token_id, trust_id, db)
    role_id = _trust_owner_role_id(db)

    grant = db.exec(
        select(UserRole)
        .where(col(UserRole.user_id) == user_id)
        .where(col(UserRole.role_id) == role_id)
        .where(col(UserRole.trust_id) == trust_id)
    ).first()
    if grant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with ID: {user_id} is not a Trust Owner of this trust",
        )

    if _owner_count(db, role_id, trust_id, exclude_grant_id=grant.id) == 0:
        logger.error(
            f"Refusing to remove the last Trust Owner ({user_id}) of trust {trust_id}: "
            f"the trust would be left with nobody able to approve, nominate an owner, or edit its policy"
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This is the trust's only owner. Nominate a replacement before removing them, "
                "so the trust is never left without anyone able to act for it."
            ),
        )

    try:
        db.delete(grant)
        audit_trust_action(
            trust_id=trust_id,
            trust_name=trust.name,
            action=TrustAuditAction.OWNER_REMOVED,
            user_id=token_id,
            session=db,
        )
        db.commit()
    except SQLAlchemyError as e:
        db.rollback()
        logger.exception(f"Error removing Trust Owner {user_id} at trust {trust_id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from e

    logger.info(f"User {token_id} removed {user_id} as a Trust Owner of trust {trust_id}")
