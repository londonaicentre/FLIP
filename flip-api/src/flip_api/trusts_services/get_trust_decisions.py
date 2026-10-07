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

import json
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlmodel import Session, col, select

from flip_api.auth.auth_utils import has_trust_permissions
from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.db.models.main_models import Projects, ProjectsAudit, ProjectTrustIntersect, Queries, QueryResult, Trust
from flip_api.db.models.user_models import PermissionRef, UserProfile
from flip_api.domain.interfaces.trust import ITrustCohortCount, ITrustDecision
from flip_api.domain.schemas.actions import ProjectAuditAction
from flip_api.domain.schemas.status import TrustApprovalStatus
from flip_api.utils.logger import logger

router = APIRouter(prefix="/trust", tags=["trusts_services"])


def _utc_iso(value: datetime | None) -> str | None:
    """ISO string of a naive-UTC column, ``Z``-suffixed so the browser reads it as UTC, not local time."""
    return value.isoformat(timespec="milliseconds") + "Z" if value else None


def _staged_at(db: Session, project_id: UUID) -> str | None:
    """When the project was last staged, from its audit trail."""
    staged = db.exec(
        select(ProjectsAudit.audit_date)
        .where(ProjectsAudit.project_id == project_id)
        .where(ProjectsAudit.action == ProjectAuditAction.STAGE)
        .order_by(col(ProjectsAudit.audit_date).desc())
        .limit(1)
    ).first()
    return _utc_iso(staged)


def _cohort(db: Session, project_id: UUID, trust_id: UUID) -> tuple[str | None, ITrustCohortCount | None]:
    """The project's latest cohort query, and the trust's answer to it if the trust returned one."""
    query = db.exec(
        select(Queries).where(Queries.project_id == project_id).order_by(col(Queries.created).desc()).limit(1)
    ).first()
    if query is None:
        return None, None
    result = db.exec(
        select(QueryResult).where(QueryResult.query_id == query.id).where(QueryResult.trust_id == trust_id)
    ).first()
    if result is None:
        return query.query, None
    data = json.loads(result.data)
    return query.query, ITrustCohortCount(
        record_count=data.get("record_count"), suppressed=bool(data.get("suppressed")), error=data.get("error")
    )  # type: ignore[call-arg]


@router.get(
    "/{trust_id}/decisions",
    summary="Projects staged at a trust, with the trust's decision on each (for its Trust Admin).",
    response_model=list[ITrustDecision],
    status_code=status.HTTP_200_OK,
)
def get_trust_decisions(
    trust_id: UUID = Path(..., description="The trust whose project decisions to list."),
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
) -> list[ITrustDecision]:
    """Projects staged at a trust, for its Trust Admin: awaiting a decision first, then decided, newest first.

    Args:
        trust_id (UUID): The trust.
        db (Session): Database session.
        user_id (UUID): The caller.

    Returns:
        list[ITrustDecision]: One entry per non-deleted project staged at the trust.

    Raises:
        HTTPException: 404 if the trust does not exist; 403 unless the caller is that trust's Trust Admin.
    """
    if db.get(Trust, trust_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Trust {trust_id} not found")
    if not has_trust_permissions(user_id, [PermissionRef.CAN_APPROVE_FOR_TRUST], trust_id, db):
        logger.error(f"User {user_id} may not list the project decisions of trust {trust_id}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"User with ID: {user_id} was unable to view these decisions",
        )

    rows = db.exec(
        select(ProjectTrustIntersect, Projects, UserProfile.name)
        .join(Projects, col(Projects.id) == col(ProjectTrustIntersect.project_id))
        .join(UserProfile, col(UserProfile.user_id) == col(ProjectTrustIntersect.decided_by), isouter=True)
        .where(ProjectTrustIntersect.trust_id == trust_id)
        .where(col(Projects.deleted).is_(False))
    ).all()

    items = []
    for intersect, project, decider_name in rows:
        owner = db.get(UserProfile, project.owner_id)
        query, cohort = _cohort(db, project.id, trust_id)
        items.append(
            ITrustDecision(
                project_id=project.id,
                project_name=project.name,
                description=project.description,
                owner_name=owner.name if owner and owner.name else None,
                project_status=project.status,
                has_imaging=project.has_imaging,
                staged_at=_staged_at(db, project.id),
                query=query,
                cohort=cohort,
                status=intersect.status,
                decided_by_name=decider_name or None,
                decided_at=_utc_iso(intersect.decided_at),
                decided_as=intersect.decided_as,
            )  # type: ignore[call-arg]
        )

    pending = sorted(
        (item for item in items if item.status == TrustApprovalStatus.PENDING),
        key=lambda item: item.staged_at or "",
        reverse=True,
    )
    decided = sorted(
        (item for item in items if item.status != TrustApprovalStatus.PENDING),
        key=lambda item: item.decided_at or "",
        reverse=True,
    )
    return pending + decided
