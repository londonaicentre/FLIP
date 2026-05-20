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

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlmodel import Session, and_, col, desc, func, or_, select

from flip_api.auth.auth_utils import has_permissions
from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.db.models.main_models import (
    Projects,
    ProjectsAudit,
    ProjectUserAccess,
    Queries,
    QueryResult,
    QueryStats,
    TrustTask,
)
from flip_api.db.models.user_models import PermissionRef, UserProfile
from flip_api.domain.interfaces.project import IProject, IProjectQuery
from flip_api.domain.schemas.actions import ProjectAuditAction
from flip_api.domain.schemas.status import TaskStatus
from flip_api.project_services.services.project_services import (
    _collect_errored_trust_ids,
    _distinct_responded_trust_ids,
    get_trusts_approval_status_for_projects,
)
from flip_api.utils.logger import logger
from flip_api.utils.paging_utils import (
    FilterInfo,
    IPagedData,
    IPagedResponse,
    PagingInfo,
    get_filter_details,
    get_paging_details,
    get_total_pages,
)

router = APIRouter(prefix="/projects", tags=["project_services"])


def get_projects_paginated_orm(
    session: Session,
    user_id: UUID | None,
    paging_details: PagingInfo,
    filter_details: FilterInfo,
) -> IPagedResponse[IProject]:
    """
    Fetches paginated project data from the database using SQLModel ORM.

    Args:
        session (Session): The SQLModel session used for the database queries.
        user_id (UUID | None): The requesting user's ID. When provided, results are restricted to
            projects the user owns or has explicit access to.
        paging_details (PagingInfo): Page size, offset, and optional search string.
        filter_details (FilterInfo): Additional filter criteria (e.g. owner ID).

    Returns:
        IPagedResponse[IProject]: Paginated list of projects and total row count.
    """
    # Extract paging and filter details
    page_size = paging_details.page_size
    search_str = paging_details.search_str
    offset = paging_details.offset
    filter_owner_id = filter_details.owner

    # Base query conditions
    # Note [not Projects.deleted] is not a valid SQL expression - it returns empty entries.
    base_conditions = [Projects.deleted.is_(False)]  # type: ignore[attr-defined]

    # Search conditions: will search in both name and description fields
    if search_str:
        search_condition = or_(
            func.lower(Projects.name).like(f"%{search_str.lower()}%"),
            func.lower(Projects.description).like(f"%{search_str.lower()}%"),
        )
        base_conditions.append(search_condition)  # type: ignore

    # Owner filter condition
    if filter_owner_id:
        base_conditions.append(Projects.owner_id == filter_owner_id)

    # User access condition (user can see projects they own or have access to)
    if user_id:
        user_access_condition = or_(
            Projects.owner_id == user_id,
            ProjectUserAccess.user_id == user_id,
        )
        base_conditions.append(user_access_condition)

    # Main query for projects with pagination. The UserProfile LEFT JOIN
    # pulls the owner's display name in the same round-trip — same pattern
    # used in _load_latest_query_per_project for Queries.created_by.
    projects_query = (
        select(Projects, UserProfile.name)  # type: ignore[call-overload]
        .outerjoin(
            ProjectUserAccess,
            and_(ProjectUserAccess.project_id == Projects.id, ProjectUserAccess.user_id == user_id),
        )
        .outerjoin(UserProfile, UserProfile.user_id == Projects.owner_id)  # type: ignore[arg-type]
        .where(and_(*base_conditions))
        .order_by(desc(Projects.creation_timestamp))
        .limit(page_size)
        .offset(offset)
    )

    # Count query for total records
    count_query = (
        select(func.count(func.distinct(Projects.id)))
        .outerjoin(
            ProjectUserAccess,
            and_(ProjectUserAccess.project_id == Projects.id, ProjectUserAccess.user_id == user_id),
        )
        .where(and_(*base_conditions))
    )

    # Execute queries
    project_rows = session.exec(projects_query).all()
    total_records = session.exec(count_query).one_or_none() or 0

    project_ids = [project.id for project, _ in project_rows]
    trusts_by_project = get_trusts_approval_status_for_projects(project_ids, session)
    queries_by_project = _load_latest_query_per_project(project_ids, session)
    staged_at_by_project = _load_latest_audit_at_per_project(project_ids, ProjectAuditAction.STAGE, session)
    user_count_by_project = _load_user_counts(project_ids, session)

    projects_response = [
        IProject(
            id=project.id,
            name=project.name,
            description=project.description,
            owner_id=project.owner_id,
            owner_name=owner_name or None,
            user_count=user_count_by_project.get(project.id, 0),
            # `Z` suffix so the browser treats the naive UTC value as UTC.
            creation_timestamp=project.creation_timestamp.isoformat(timespec="milliseconds") + "Z",
            staged_at=staged_at_by_project.get(project.id),
            status=project.status,
            approved_trusts=trusts_by_project.get(project.id, []),
            query=queries_by_project.get(project.id),
        )  # type: ignore[call-arg]
        for project, owner_name in project_rows
    ]

    return IPagedResponse[IProject](data=projects_response, total_rows=total_records)


def _load_latest_query_per_project(
    project_ids: list[UUID],
    session: Session,
) -> dict[UUID, IProjectQuery]:
    """Batch-load most-recent cohort query + cohort size for each project.

    Avoids the N+1 cost of calling the per-project query loader on every row.
    Three queries total: pick the latest Queries row per project, then batch the
    trust counts and the cohort totals for the picked query IDs.

    Args:
        project_ids: Project IDs to load queries for.
        session: Active SQLModel session.

    Returns:
        Mapping project_id → ``IProjectQuery`` for projects that have at least
        one cohort query. Projects without one are omitted from the result.
    """
    if not project_ids:
        return {}

    # All queries for these projects, ordered so the latest per project sorts first.
    # LEFT JOIN UserProfile so the response carries the runner's display name
    # alongside the timestamp ("Last run … by R. Patel"); the name is null when the
    # runner has no UserProfile row.
    queries_rows = session.exec(
        select(  # type: ignore[call-overload]
            Queries.id,
            Queries.project_id,
            Queries.name,
            Queries.query,
            Queries.created,
            UserProfile.name,
            Queries.queried_trust_ids,
        )
        .join(UserProfile, UserProfile.user_id == Queries.created_by, isouter=True)
        .where(col(Queries.project_id).in_(project_ids))
        .order_by(col(Queries.project_id), col(Queries.created).desc())
    ).all()

    latest_per_project: dict[
        UUID, tuple[UUID, str, str, datetime | None, str | None, list[UUID]]
    ] = {}
    for qid, pid, qname, qsql, qcreated, qcreated_by, persisted_queried in queries_rows:
        if pid is not None and pid not in latest_per_project:
            latest_per_project[pid] = (qid, qname, qsql, qcreated, qcreated_by, persisted_queried)

    if not latest_per_project:
        return {}

    query_ids = [qid for qid, _, _, _, _, _ in latest_per_project.values()]

    # QueryResult rows give us the responded set + errored carve-out per
    # query. Volume is bounded (queries-in-page × trusts).
    errored_trust_ids_by_query: dict[UUID, list[UUID]] = {qid: [] for qid in query_ids}
    responded_trust_ids_by_query: dict[UUID, list[UUID]] = {qid: [] for qid in query_ids}
    rows_by_query: dict[UUID, list[tuple[UUID | None, str | None]]] = {qid: [] for qid in query_ids}
    pair_rows = session.exec(
        select(QueryResult.query_id, QueryResult.trust_id, QueryResult.data)
        .where(col(QueryResult.query_id).in_(query_ids))
    ).all()
    for qid, tid, data in pair_rows:
        if qid is None:
            continue
        rows_by_query.setdefault(qid, []).append((tid, data))
    for qid, rows in rows_by_query.items():
        errored_trust_ids_by_query[qid] = _collect_errored_trust_ids(rows, query_id=qid)
        responded_trust_ids_by_query[qid] = _distinct_responded_trust_ids(rows)

    # Trust task state per query, split by status:
    #   PENDING   → "queued" chip (queued at hub, trust hasn't polled)
    #   CANCELLED → "skipped" chip (project approved without the trust)
    # Once a trust polls, PENDING moves to IN_PROGRESS and drops out of
    # both sets.
    pending_trust_ids_by_query: dict[UUID, list[UUID]] = {qid: [] for qid in query_ids}
    cancelled_trust_ids_by_query: dict[UUID, list[UUID]] = {qid: [] for qid in query_ids}
    task_rows = session.exec(
        select(TrustTask.query_id, TrustTask.trust_id, TrustTask.status)
        .where(
            col(TrustTask.query_id).in_(query_ids),
            col(TrustTask.status).in_([TaskStatus.PENDING, TaskStatus.CANCELLED]),
        )
    ).all()
    for qid, tid, task_status in task_rows:
        if qid is None or tid is None:
            continue
        bucket = (
            pending_trust_ids_by_query
            if task_status == TaskStatus.PENDING
            else cancelled_trust_ids_by_query
        )
        if tid not in bucket.setdefault(qid, []):
            bucket[qid].append(tid)

    total_cohort_by_query: dict[UUID, int] = {}
    stats_rows = session.exec(
        select(QueryStats.query_id, QueryStats.stats).where(col(QueryStats.query_id).in_(query_ids))
    ).all()
    for stats_qid, stats_json in stats_rows:
        if stats_qid is None or not stats_json:
            continue
        try:
            parsed = json.loads(stats_json)
        except (ValueError, TypeError):
            continue
        # The detail endpoint accepts either key — keep the same fallback to
        # avoid surprising shape divergence between list and detail responses.
        total_cohort_by_query[stats_qid] = int(parsed.get("TotalCount") or parsed.get("record_count") or 0)

    return {
        pid: IProjectQuery(
            id=qid,
            name=qname,
            query=qsql,
            queried_trust_ids=list(persisted_queried),
            pending_trust_ids=pending_trust_ids_by_query.get(qid, []),
            cancelled_trust_ids=cancelled_trust_ids_by_query.get(qid, []),
            responded_trust_ids=responded_trust_ids_by_query.get(qid, []),
            errored_trust_ids=errored_trust_ids_by_query.get(qid, []),
            total_cohort=total_cohort_by_query.get(qid, 0),
            # `Z` suffix so the browser parses as UTC (naive UTC column).
            created=(qcreated.isoformat(timespec="milliseconds") + "Z") if qcreated else None,
            created_by=qcreated_by or None,
        )  # type: ignore[call-arg]
        for pid, (qid, qname, qsql, qcreated, qcreated_by, persisted_queried) in latest_per_project.items()
    }


def _load_latest_audit_at_per_project(
    project_ids: list[UUID],
    action: ProjectAuditAction,
    session: Session,
) -> dict[UUID, str]:
    """Batch-load the most recent ``projects_audit`` timestamp per project for a given action.

    One SQL round-trip — useful for surfacing stage transition dates (STAGE,
    APPROVE, UNSTAGE, etc.) without paying the N+1 cost of per-project audit
    lookups.
    """
    if not project_ids:
        return {}

    rows = session.exec(
        select(ProjectsAudit.project_id, ProjectsAudit.audit_date)
        .where(
            col(ProjectsAudit.project_id).in_(project_ids),
            col(ProjectsAudit.action) == action,
        )
        .order_by(col(ProjectsAudit.project_id), col(ProjectsAudit.audit_date).desc())
    ).all()

    latest: dict[UUID, str] = {}
    for pid, audit_date in rows:
        if pid is None or audit_date is None or pid in latest:
            continue
        latest[pid] = audit_date.isoformat(timespec="milliseconds")

    return latest


def _load_user_counts(project_ids: list[UUID], session: Session) -> dict[UUID, int]:
    """Per-project user count.

    The owner is auto-added to ``ProjectUserAccess`` on project creation, so
    this count includes them and the UI doesn't need to ``+1``.
    """
    if not project_ids:
        return {}

    rows = session.exec(
        select(ProjectUserAccess.project_id, func.count(col(ProjectUserAccess.user_id)))
        .where(col(ProjectUserAccess.project_id).in_(project_ids))
        .group_by(col(ProjectUserAccess.project_id))
    ).all()

    return {pid: int(count or 0) for pid, count in rows if pid is not None}


# [#114] ✅
@router.get(
    "",
    summary="Get a paginated and filtered list of projects.",
    response_model=IPagedData[IProject],
    status_code=status.HTTP_200_OK,
)
def get_projects_endpoint(
    request: Request,
    session: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
) -> IPagedData[IProject]:
    """
    Get a paginated and filtered list of projects.

    Args:
        request (Request): The HTTP request object, used to access query parameters.
        session (Session): The database session for querying.
        user_id (UUID): The ID of the user making the request.

    Returns:
        IPagedData[IProject]: A paginated response containing the projects.

    Raises:
        HTTPException: If an error occurs while fetching projects.
    """
    logger.info("Requesting all projects")

    paging_details = get_paging_details(query_string_parameters=dict(request.query_params))
    filter_details = get_filter_details(query_string_parameters=dict(request.query_params))

    # If user has manager permissions, remove their user-specific filter (equivalent to `user_id = null`)
    if has_permissions(user_id, [PermissionRef.CAN_MANAGE_PROJECTS], session):
        logger.debug(f"User with ID {user_id} can manage all projects, removing user access filter from query.")
        requesting_user_id = None
    else:
        requesting_user_id = user_id

    try:
        project_response = get_projects_paginated_orm(
            session=session,
            user_id=requesting_user_id,
            paging_details=paging_details,
            filter_details=filter_details,
        )
    except Exception as exc:
        logger.error(f"Error fetching projects: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error occurred while fetching projects."
        )

    data = project_response.data
    logger.info(f"Found {len(data)} projects.")

    total_records = project_response.total_rows
    total_pages = get_total_pages(total_records, paging_details.page_size)

    data_to_return: IPagedData[IProject] = IPagedData(
        page=paging_details.page_number,
        page_size=paging_details.page_size,
        total_pages=total_pages,
        total_records=total_records,
        data=data,
    )  # type: ignore[call-arg]

    logger.debug(f"Returning paginated project data: {data_to_return}")

    return data_to_return
