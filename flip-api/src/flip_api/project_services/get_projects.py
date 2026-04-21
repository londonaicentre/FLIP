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

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlmodel import Session, and_, desc, func, or_, select

from flip_api.auth.auth_utils import has_permissions
from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.db.models.main_models import Projects, ProjectUserAccess
from flip_api.db.models.user_models import PermissionRef
from flip_api.domain.interfaces.project import IProject
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

    # Main query for projects with pagination
    projects_query = (
        select(Projects)
        .outerjoin(
            ProjectUserAccess,
            and_(ProjectUserAccess.project_id == Projects.id, ProjectUserAccess.user_id == user_id),
        )
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
    project_results = session.exec(projects_query).all()
    total_records = session.exec(count_query).one_or_none() or 0

    # Convert results to list of IProject
    projects_response = [
        IProject(
            id=project.id,
            name=project.name,
            description=project.description,
            owner_id=project.owner_id,
            creation_timestamp=project.creation_timestamp.isoformat(timespec="milliseconds"),
            status=project.status,
        )  # type: ignore[call-arg]
        for project in project_results
    ]

    return IPagedResponse[IProject](data=projects_response, total_rows=total_records)


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
