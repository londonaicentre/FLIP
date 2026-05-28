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

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlmodel import Session, col, select

from flip_api.auth.auth_utils import has_permissions
from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.db.models.main_models import ProjectTrustIntersect, Trust
from flip_api.db.models.user_models import PermissionRef
from flip_api.domain.interfaces.trust import IAdminTrust
from flip_api.utils.logger import logger

router = APIRouter(prefix="/admin/trusts", tags=["trusts_services"])


def _as_utc_iso(value: datetime | None) -> str | None:
    """Serialise a naive UTC datetime as an ISO-8601 string with a `Z` marker.

    The Trust columns are `timestamp without time zone` but values are written
    via `datetime.now(timezone.utc)` — so they're already UTC, the timezone is
    just stripped on persist. Tagging the wire format prevents the browser from
    treating the naive ISO as local time.
    """
    if value is None:
        return None

    return value.isoformat(timespec="milliseconds") + "Z"


@router.get("", response_model=list[IAdminTrust], status_code=status.HTTP_200_OK)
def admin_get_trusts(
    db: Session = Depends(get_session),
    token_id: UUID = Depends(verify_token),
) -> list[IAdminTrust]:
    """List every trust with admin-level fields.

    Args:
        db (Session): Database session.
        token_id (UUID): Authenticated user ID, used for the admin permission check.

    Returns:
        list[IAdminTrust]: Every trust with name, created_at, last_heartbeat,
        and project_count (count of rows in ``project_trust_intersect``).

    Raises:
        HTTPException: 403 if the caller lacks ``CAN_ACCESS_ADMIN_PANEL``;
            500 on database error.
    """
    if not has_permissions(token_id, [PermissionRef.CAN_ACCESS_ADMIN_PANEL], db):
        logger.error(f"User {token_id} attempted to list trusts without admin permission")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin permission required to list trusts.",
        )

    try:
        trusts = db.exec(select(Trust)).all()

        # One round-trip to compute per-trust project counts.
        count_rows = db.exec(
            select(ProjectTrustIntersect.trust_id, func.count(col(ProjectTrustIntersect.id))).group_by(
                col(ProjectTrustIntersect.trust_id)
            )
        ).all()
        counts: dict[UUID, int] = {row[0]: row[1] for row in count_rows if row[0] is not None}

        return [
            IAdminTrust(
                id=t.id,
                name=t.name,
                code=t.code,
                region=t.region,
                created_at=_as_utc_iso(t.created_at),
                last_heartbeat=_as_utc_iso(t.last_heartbeat),
                project_count=counts.get(t.id, 0),
            )
            for t in trusts
        ]
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error listing trusts for admin")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from e
