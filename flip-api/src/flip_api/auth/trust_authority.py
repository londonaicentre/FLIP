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

"""Who may decide on a project at a trust (FLIP#1258).

A trust with at least one Trust Admin decides for itself: only a holder of CAN_APPROVE_FOR_TRUST at that trust may
approve or decline there, and the hub admin may not. A trust with none is decided by the hub admin through the
global CAN_APPROVE_PROJECTS, as before site approval existed. There is no separate setting — appointing a trust's
first Trust Admin hands its decisions to the site, and removing its last hands them back.
"""

from collections.abc import Iterable
from uuid import UUID

from sqlmodel import Session, col, select

from flip_api.auth.auth_utils import has_permissions, has_trust_permissions
from flip_api.db.models.user_models import PermissionRef, RoleRef, UserRole
from flip_api.domain.schemas.status import DecisionMaker


def trusts_with_admin(trust_ids: Iterable[UUID], db: Session) -> set[UUID]:
    """The subset of ``trust_ids`` that have at least one Trust Admin.

    Args:
        trust_ids (Iterable[UUID]): Trusts to check.
        db (Session): Database session.

    Returns:
        set[UUID]: Trusts that decide for themselves.
    """
    ids = list(trust_ids)
    if not ids:
        return set()
    rows = db.exec(
        select(UserRole.trust_id)
        .where(UserRole.role_id == RoleRef.TRUST_ADMIN.value)
        .where(col(UserRole.trust_id).in_(ids))
        .distinct()
    ).all()
    return {trust_id for trust_id in rows if trust_id is not None}


def decision_maker_for(
    user_id: UUID, trust_id: UUID, db: Session, *, has_admin: bool | None = None
) -> DecisionMaker | None:
    """How ``user_id`` may decide at ``trust_id``: as the SITE, as the HUB, or not at all.

    Args:
        user_id (UUID): The caller.
        trust_id (UUID): The trust being decided.
        db (Session): Database session.
        has_admin (bool | None): Whether the trust has a Trust Admin, when the caller already knows (batch
            callers); looked up when None.

    Returns:
        DecisionMaker | None: SITE or HUB, or None when the caller holds no authority there.
    """
    if has_admin is None:
        has_admin = trust_id in trusts_with_admin([trust_id], db)
    if has_admin:
        permitted = has_trust_permissions(user_id, [PermissionRef.CAN_APPROVE_FOR_TRUST], trust_id, db)
        return DecisionMaker.SITE if permitted else None
    return DecisionMaker.HUB if has_permissions(user_id, [PermissionRef.CAN_APPROVE_PROJECTS], db) else None
