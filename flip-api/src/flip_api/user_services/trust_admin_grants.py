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

"""Trust Admin grants (FLIP#1258).

A Trust Admin is a Researcher who can also approve or decline projects for one trust. It is stored as two
``user_role`` rows — a global Researcher row and a Trust Admin row at the trust — so a trust grant never confers
anything platform-wide (FLIP#1260), and the Users tab presents the pair as one role.
"""

from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session, col, select

from flip_api.db.models.main_models import Trust
from flip_api.db.models.user_models import RoleRef, UserRole
from flip_api.domain.schemas.actions import TrustAuditAction
from flip_api.domain.schemas.users import ITrustAdminOf
from flip_api.trusts_services.utils.audit_helper import audit_trust_action


@dataclass(frozen=True)
class RoleGrants:
    """The rows a role request becomes.

    Attributes:
        global_role_ids (list[UUID]): Roles held platform-wide (``trust_id IS NULL``).
        trust_admin_trust_id (UUID | None): The trust the user administers, or None.
    """

    global_role_ids: list[UUID]
    trust_admin_trust_id: UUID | None


def resolve_role_grants(role_ids: list[UUID], trust_id: UUID | None, db: Session) -> RoleGrants:
    """Validate a role request and turn it into the grants to store.

    Args:
        role_ids (list[UUID]): The requested roles.
        trust_id (UUID | None): The trust named with the request.
        db (Session): Database session.

    Returns:
        RoleGrants: Trust Admin becomes global Researcher plus the trust; any other role passes through.

    Raises:
        HTTPException: 400 if Trust Admin is combined with another role or given no trust, or a trust is named
            with any other role; 404 if the trust does not exist.
    """
    if RoleRef.TRUST_ADMIN.value not in role_ids:
        if trust_id is not None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Only the Trust Admin role is held at a trust.")
        return RoleGrants(global_role_ids=role_ids, trust_admin_trust_id=None)
    if len(role_ids) != 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Trust Admin is a user's only role.")
    if trust_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Choose the trust a Trust Admin administers.")
    if db.get(Trust, trust_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Trust {trust_id} not found")
    return RoleGrants(global_role_ids=[RoleRef.RESEARCHER.value], trust_admin_trust_id=trust_id)


def _trust_admin_rows(user_id: UUID, db: Session) -> list[UserRole]:
    return list(
        db.exec(
            select(UserRole)
            .where(UserRole.user_id == user_id)
            .where(UserRole.role_id == RoleRef.TRUST_ADMIN.value)
            .where(col(UserRole.trust_id).is_not(None))
        ).all()
    )


def _audit(trust_id: UUID, action: TrustAuditAction, user_id: UUID, acting_user_id: UUID, db: Session) -> None:
    trust = db.get(Trust, trust_id)
    audit_trust_action(
        trust_id=trust_id,
        trust_name=trust.name if trust else "",
        action=action,
        user_id=acting_user_id,
        session=db,
        subject_user_id=user_id,
    )


def apply_trust_admin_grant(user_id: UUID, trust_id: UUID | None, acting_user_id: UUID, db: Session) -> None:
    """Make ``trust_id`` the user's only Trust Admin grant (None removes any), auditing each change.

    Does not commit: the caller writes the global rows in the same transaction.

    Args:
        user_id (UUID): The user whose grant changes.
        trust_id (UUID | None): The trust they administer from now on, or None.
        acting_user_id (UUID): The admin making the change, for the audit rows.
        db (Session): Database session.
    """
    current = _trust_admin_rows(user_id, db)
    for row in current:
        if row.trust_id is not None and row.trust_id != trust_id:
            db.delete(row)
            _audit(row.trust_id, TrustAuditAction.ADMIN_REMOVED, user_id, acting_user_id, db)
    if trust_id is not None and all(row.trust_id != trust_id for row in current):
        db.add(UserRole(user_id=user_id, role_id=RoleRef.TRUST_ADMIN.value, trust_id=trust_id))
        _audit(trust_id, TrustAuditAction.ADMIN_ADDED, user_id, acting_user_id, db)


def get_trust_admin_of(user_id: UUID, db: Session) -> ITrustAdminOf | None:
    """The trust a user administers, or None.

    Args:
        user_id (UUID): The user.
        db (Session): Database session.

    Returns:
        ITrustAdminOf | None: The trust, or None if the user is not a Trust Admin.
    """
    trust = db.exec(
        select(Trust)
        .join(UserRole, col(UserRole.trust_id) == col(Trust.id))
        .where(UserRole.user_id == user_id)
        .where(UserRole.role_id == RoleRef.TRUST_ADMIN.value)
        .order_by(col(Trust.name))
    ).first()
    return ITrustAdminOf(id=trust.id, code=trust.code, name=trust.name) if trust else None
