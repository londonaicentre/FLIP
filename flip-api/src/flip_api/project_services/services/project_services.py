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
from typing import List, Optional, Tuple
from uuid import UUID

from fastapi import HTTPException
from psycopg2 import DatabaseError
from sqlalchemy import delete, desc, func
from sqlmodel import Session, col, or_, select

# Assume these models and schemas are defined in your project
from flip_api.db.models.main_models import (
    Model,
    Projects,
    ProjectTrustIntersect,
    ProjectUserAccess,
    Queries,
    QueryResult,
    QueryStats,
    Trust,
    XNATProjectStatus,
)
from flip_api.domain.interfaces.project import (
    IApprovedTrust,
    IModelsInfoResponse,
    IProjectApproval,
    IProjectDetails,
    IProjectQuery,
    IProjectResponse,
    IReimportQuery,
)
from flip_api.domain.schemas.actions import ProjectAuditAction
from flip_api.domain.schemas.projects import (
    ProjectDetails,
    UserAccessInfo,
)
from flip_api.domain.schemas.status import (
    ProjectStatus,
    XNATImageStatus,
)
from flip_api.model_services.services.model_service import delete_models
from flip_api.project_services.utils.audit_helper import audit_project_action
from flip_api.utils.logger import logger
from flip_api.utils.paging_utils import IPagedResponse, PagingInfo, get_paging_details


def update_project_user_access(project_id: UUID, user_ids: List[UUID], session: Session) -> None:
    """
    Updates the user access for a project by creating new ProjectUserAccess entries for the provided user IDs.

    Args:
        project_id (UUID): The ID of the project for which to update user access.
        user_ids (List[UUID]): A list of user IDs to grant access to the project.
        session (Session): The SQLModel session to use for database operations.

    Returns:
        None
    """
    # Convert EmailStr to UUID
    # user_pool_id = get_settings().AWS_COGNITO_USER_POOL_ID
    # users = [get_user_by_email_or_id(user_pool_id=user_pool_id, email=uid) for uid in user_emails]
    access_entries = []
    for user_id in user_ids:
        # if not user.id:
        #     logger.warning(f"User {user.email} does not have a valid ID, skipping access update.")
        #     continue

        # Create ProjectUserAccess entries
        access_entry = ProjectUserAccess(
            project_id=project_id,
            user_id=user_id,
        )
        access_entries.append(access_entry)
    session.add_all(access_entries)
    session.commit()  # Commit the changes to the database
    session.flush()
    logger.info(f"Updated user access for project {project_id} with {len(user_ids)} users.")


def create_project(
    payload: ProjectDetails,
    current_user_id: UUID,
    session: Session,
) -> UUID:
    """
    Creates a new project and assigns user access.

    Args:
        payload (IProjectDetails): The project details including name, description, and user IDs.
        current_user_id (UUID): The ID of the user creating the project.
        session (Session): The SQLModel session to use for database operations.

    Returns:
        UUID: The ID of the newly created project.

    Raises:
        HTTPException: If the request cannot be processed.
    """
    try:
        # Create the project instance
        new_project = Projects(
            name=payload.name,
            description=payload.description,
            owner_id=current_user_id,
            status=ProjectStatus.UNSTAGED,  # Default status
            creation_timestamp=datetime.utcnow(),
        )
        session.add(new_project)
        session.flush()  # Ensure the project is added and has an ID
        access_entry = ProjectUserAccess(
            project_id=new_project.id,
            user_id=current_user_id,  # Add the creator as the first user
        )
        session.add(access_entry)
        session.flush()  # Ensures new_project.id is available
        session.commit()  # Commit to finalize the project creation
        project_id = new_project.id
        if not project_id:  # Should not happen if flush is successful
            raise ValueError("Failed to retrieve ID for newly created project.")

        payload.users = payload.users
        if payload.users:
            update_project_user_access(project_id, payload.users, session)

        # Commit is typically handled by the calling endpoint or a 'with session.begin():' block
        logger.info(f"Project {project_id} created successfully by user {current_user_id}.")
        return project_id

    except DatabaseError as e:
        session.rollback()
        logger.error(f"Error creating project: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create project: {e}",
        )


def delete_project(project_id: UUID, current_user_id: UUID, session: Session):
    """
    Marks a project as deleted and handles related cleanup.
    This is a soft delete, meaning the project is marked as deleted but not removed from the database.

    Args:
        project_id (UUID): The ID of the project to delete.
        current_user_id (UUID): The ID of the user performing the deletion.
        session (Session): The SQLModel session to use for database operations.

    Returns:
        None

    Raises:
        ValueError: If the project does not exist or is already marked as deleted.
        HTTPException: If the project cannot be deleted due to an error.
    """
    try:
        project = session.get(Projects, project_id)

        if not project:
            logger.warn(f"Project {project_id} not found for deletion.")
            raise ValueError(f"Project with ID {project_id} not found.")

        if project.deleted:
            logger.info(f"Project {project_id} is already marked as deleted.")
            return  # Or raise an error if trying to delete an already deleted project

        # Soft delete the project
        project.deleted = True
        session.add(project)

        session.flush()

        # Audit the deletion
        audit_project_action(
            project_id=project_id,
            action=ProjectAuditAction.DELETE,
            user_id=current_user_id,
            session=session,
        )

        # Delete related models (not hard delete if ensure_deletion=False)
        models_deleted_count = delete_models(
            project_id=project_id,
            user_id=str(current_user_id),
            session=session,
            ensure_deletion=False,
        )

        if not models_deleted_count:
            logger.warn(f"No models deleted for project {project_id} during project deletion.")
        else:
            logger.info(f"{models_deleted_count} models deleted for project {project_id}.")

        logger.info(f"Project {project_id} marked as deleted by user {current_user_id}.")

        session.commit()

    except Exception as e:
        session.rollback()
        logger.error(f"Error deleting project {project_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete project: {e}",
        )


def edit_project_service(
    project_id: UUID,
    payload: IProjectDetails,
    current_user_id: UUID,
    session: Session,
) -> None:
    """
    Updates project details and user access.

    Args:
        project_id (UUID): The ID of the project to edit.
        payload (IProjectDetails): The new project details including name, description, and user IDs.
        current_user_id (UUID): The ID of the user performing the edit action.
        session (Session): The SQLModel session to use for database operations.

    Returns:
        None

    Raises:
        ValueError: If the project does not exist or is deleted.
        HTTPException: If the project cannot be updated due to an error.
    """
    try:
        # Check if project exists and is not deleted
        project = session.get(Projects, project_id)
        if not project or project.deleted:
            raise ValueError(f"Project {project_id} does not exist or is deleted, cannot edit.")

        project.name = payload.name
        project.description = payload.description
        session.add(project)

        # Delete all existing access records
        access_entries = session.exec(select(ProjectUserAccess).where(ProjectUserAccess.project_id == project_id)).all()

        for entry in access_entries:
            session.delete(entry)

        session.flush()  # Apply deletes before re-adding

        # Add new access entries
        if payload.users:
            update_project_user_access(project_id, payload.users, session)

        logger.debug(f"Audit project action for editing project {project_id} by user {current_user_id}.")

        audit_project_action(
            project_id=project_id,
            action=ProjectAuditAction.EDIT,
            user_id=current_user_id,
            session=session,
        )

        logger.info(f"Project {project_id} updated successfully by user {current_user_id}.")
        session.commit()
        return

    except Exception as e:
        logger.error(f"Error editing project {project_id}: {e}")
        session.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to edit project: {e}",
        )


def get_project_query(project_from_db: IProjectResponse) -> Optional[IProjectQuery] | None:
    """
    If the project has a query, try to return it if it is valid. Otherwise, return None.

    Args:
        project_from_db (IProjectResponse): The project response object retrieved from the database, which may contain
        a query.

    Returns:
        Optional[IProjectQuery]: The project query if it exists and is valid, otherwise None.
    """
    logger.debug(project_from_db)
    query = project_from_db.query

    if query:
        logger.debug(query)

        if query.id and query.trusts_queried:
            return query

        logger.warning("Unable to parse query. Assuming there isn't one.")

    logger.warning("Project has no query associated with it. Returning empty.")
    return None


def get_approved_trusts_for_project(project_id: UUID, session: Session) -> List[Trust]:
    """
    Gets trust that are approved for a specific project.

    Args:
        project_id (UUID): The ID of the project to get approved trusts for.
        session (Session): The SQLModel session to use for database operations.

    Returns:
        List[Trust]: A list of Trust objects that are approved for the specified project.
    """
    stmt = (
        select(Trust.id, Trust.name, Trust.endpoint)
        .join(ProjectTrustIntersect, col(ProjectTrustIntersect.trust_id) == Trust.id)
        .where(col(ProjectTrustIntersect.project_id) == project_id)
        .where(ProjectTrustIntersect.approved)
    )
    results = session.execute(stmt).all()
    if not results:
        # Original TS code throws an error if no response.
        # Consider if returning empty list is more Pythonic for "not found" scenarios.
        logger.warn(f"No approved trusts found for project {project_id}, or project has no approved trusts.")
        # raise ValueError(f"No approved trusts found for project {project_id}") # If error is desired
    return [Trust(id=r.id, name=r.name, endpoint=r.endpoint) for r in results]


def get_trusts_approval_status_for_project(project_id: UUID, session: Session) -> List[IApprovedTrust]:
    """
    Gets all trusts linked to a project and their approval status.

    Args:
        project_id (UUID): The ID of the project to get trusts and their approval status for.
        session (Session): The SQLModel session to use for database operations.

    Returns:
        List[IApprovedTrust]: A list of IApprovedTrust objects containing trust details and their approval status for
        the specified project.
    """
    # This query assumes ProjectTrustIntersect has all trusts linked to a project,
    # and COALESCE handles trusts not yet explicitly approved/denied if they are in the intersect table.
    # If you need ALL trusts from the Trusts table and then their status for *this* project,
    # a LEFT JOIN from Trusts to ProjectTrustIntersect would be more appropriate.
    # The original query was an INNER JOIN, so it only returns trusts *present* in ProjectTrustIntersect
    # for that project.
    stmt = (
        select(
            Trust.id,
            Trust.name,
            # SQLModel doesn't directly support COALESCE in select like this easily without raw SQL or complex
            # expressions.
            # It's often easier to fetch the boolean and handle None in Python or use a raw query for COALESCE.
            ProjectTrustIntersect.approved,
        )
        .join(ProjectTrustIntersect, col(Trust.id) == ProjectTrustIntersect.trust_id)
        .where(col(ProjectTrustIntersect.project_id) == project_id)
    )
    results = session.execute(stmt).all()

    if not results:
        logger.warn(f"No trusts found linked to project {project_id} in ProjectTrustIntersect.")
        # Original TS code throws an error.
        # raise ValueError(f"No trusts found linked to project {project_id}")

    return [
        IApprovedTrust(id=r.id, name=r.name, approved=r.approved if r.approved is not None else False) for r in results
    ]


def get_project_models_service(
    project_id: UUID,
    session: Session,
    query_params: dict = {},
    all_results: bool = False,
) -> Tuple[IPagedResponse[IModelsInfoResponse], PagingInfo]:
    """
    Retrieves models for a project with pagination and optional search.

    Args:
        project_id (UUID): The ID of the project to retrieve models for.
        session (Session): The SQLModel session to use for database operations.
        query_params (dict, optional): Query parameters for pagination and search. Defaults to {}.
        all_results (bool, optional): Whether to retrieve all results without pagination. Defaults to False.

    Returns:
        Tuple[IPagedResponse[IModelsInfoResponse], PagingInfo]: A tuple containing the paged response with model info
        and paging details.
    """
    paging_details = get_paging_details(query_string_parameters=query_params)
    logger.debug(f"Paging details: {paging_details}")

    # NOTE Do NOT use "not Model.deleted" below — This expression is evaluated immediately in Python, not translated
    # into SQL. Use "Model.deleted == False" or the below to create a proper SQL condition.
    query_stmt = (
        select(Model)
        .join(Projects, col(Model.project_id) == Projects.id)
        .where(col(Model.project_id) == project_id)
        .where(col(Model.deleted).is_(False))
        .where(col(Projects.deleted).is_(False))
    )
    count_stmt = (
        select(func.count(col(Model.id)))
        .join(Projects, col(Model.project_id) == Projects.id)
        .where(col(Model.project_id) == project_id)
        .where(col(Model.deleted).is_(False))
        .where(col(Projects.deleted).is_(False))
    )

    if paging_details.search_str:
        search_pattern = f"%{paging_details.search_str.lower()}%"
        search_filter = or_(
            func.lower(Model.name).like(search_pattern),
            func.lower(Model.description).like(search_pattern),
        )
        query_stmt = query_stmt.where(search_filter)
        count_stmt = count_stmt.where(search_filter)

    total_rows = session.execute(count_stmt).scalar_one_or_none() or 0

    # Order and paginate
    query_stmt = query_stmt.order_by(desc(col(Model.creation_timestamp)))
    if not all_results and paging_details.page_size is not None:
        query_stmt = query_stmt.limit(paging_details.page_size).offset(paging_details.offset)

    db_models = session.exec(query_stmt).all()
    logger.debug(f"Models retrieved for project {project_id}: {db_models}")

    models_response = [
        IModelsInfoResponse(
            id=model.id,
            name=model.name,
            description=model.description,
            status=model.status,
            owner_id=model.owner_id,
        )
        for model in db_models
    ]
    logger.debug(f"{models_response=}")

    return IPagedResponse[IModelsInfoResponse](data=models_response, total_rows=total_rows), paging_details


def get_users_with_access_service(project_id: UUID, session: Session) -> List[UserAccessInfo]:
    """
    Retrieves a list of users who have access to a specific project.

    Args:
        project_id (UUID): The ID of the project to retrieve user access information for.
        session (Session): The SQLModel session to use for database operations.

    Returns:
        List[UserAccessInfo]: A list of UserAccessInfo objects containing user IDs of those who have access to the
        project.
    """
    stmt = select(ProjectUserAccess.user_id).where(ProjectUserAccess.project_id == project_id)
    results = session.execute(stmt).all()
    return [UserAccessInfo(user_id=uid) for uid in results]


def update_project_status(
    project_id: UUID,
    new_status: ProjectStatus,
    session: Session,
) -> None:
    """
    Updates the status of a project.

    Args:
        project_id (UUID): The ID of the project to update.
        new_status (ProjectStatus): The new status to set for the project.
        session (Session): The SQLModel session to use for database operations.

    Returns:
        None

    Raises:
        HTTPException: If the project status cannot be updated due to an error.
    """
    project = session.get(Projects, project_id)
    if not project:
        raise ValueError(f"Project {project_id} not found for status update.")

    # Try to update the status value of the project in the database
    try:
        project.name = project.name
        project.status = new_status
        session.flush()
        logger.info(f"Project {project_id} status updated to {new_status.value}.")
    except Exception as e:
        logger.error(f"Error updating status of project {project_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update status of project {project_id}: {e}",
        )


def approve_project(
    db: Session,
    project_approval: IProjectApproval,
    user_id: UUID,
) -> bool:
    """
    Approves a project for specified trusts.

    Args:
        db (Session): SQLModel session for database operations.
        project_approval (IProjectApproval): Contains project_id and trust_ids to approve.
        user_id (UUID): The ID of the user performing the approval action.

    Returns:
        bool: True if the approval was successful, False otherwise.
    """
    logger.debug("Attempting to update which trusts have been approved...")

    #
    project_id = project_approval.project_id
    trust_ids = project_approval.trust_ids

    # Check if project exists and is not deleted
    project = db.get(Projects, project_id)
    if not project or project.deleted:
        raise ValueError(f"Project {project_id} does not exist or is deleted, cannot approve.")

    for trust_id in trust_ids:
        stmt = select(ProjectTrustIntersect).where(
            ProjectTrustIntersect.project_id == project_id, ProjectTrustIntersect.trust_id == trust_id
        )
        result = db.exec(stmt).one_or_none()

        if not result:
            logger.warning(f"Trust {trust_id} not found for project {project_id}")
            db.rollback()
            return False

        result.approved = True
        db.add(result)  # Mark for update

    logger.info("Updated the trusts that have been selected for approval")

    update_project_status(
        project_id=project_id,
        new_status=ProjectStatus.APPROVED,
        session=db,
    )

    audit_response = audit_project_action(
        project_id=project_id,
        action=ProjectAuditAction.APPROVE,
        user_id=user_id,
        session=db,
    )

    logger.info(f"Audit response: {audit_response}")
    logger.info(f"Successfully approved project {project_id} for trusts: {trust_ids}")

    logger.info("Attempting to commit the changes...")
    db.commit()
    logger.info("Changes committed successfully.")

    return True


def stage_project_service(
    project_id: UUID,
    trust_ids: List[UUID],
    current_user_id: UUID,  # Added for consistency and potential auditing
    session: Session,
) -> None:
    """
    Stages a project by creating entries in ProjectTrustIntersect (unapproved).

    Args:
        project_id (UUID): The ID of the project to stage.
        trust_ids (List[UUID]): List of Trust IDs to stage the project for.
        current_user_id (UUID): The ID of the user performing the action, for auditing.
        session (Session): SQLModel session for database operations.

    Returns:
        None

    Raises:
        ValueError: If the project does not exist or is deleted.
    """
    # Check if project exists and is not deleted
    project = session.get(Projects, project_id)
    if not project or project.deleted:
        raise ValueError(f"Project {project_id} does not exist or is deleted, cannot stage.")

    if not trust_ids:
        logger.info(f"No trust IDs provided for staging project {project_id}.")
        return

    # First, remove any existing entries for this project to ensure clean staging
    stmt_delete = delete(ProjectTrustIntersect).where(col(ProjectTrustIntersect.project_id) == project_id)
    session.execute(stmt_delete)
    session.flush()

    # Create new ProjectTrustIntersect entries for each trust
    entries = [ProjectTrustIntersect(project_id=project_id, trust_id=tid, approved=False) for tid in trust_ids]
    session.add_all(entries)
    session.flush()  # Ensure entries are written

    # Update project status and add audit entry
    update_project_status(project_id=project_id, new_status=ProjectStatus.STAGED, session=session)
    audit_project_action(
        project_id=project_id,
        action=ProjectAuditAction.STAGE,
        user_id=current_user_id,
        session=session,
    )
    # Commit the transaction
    session.commit()

    logger.info(f"Project {project_id} staged for {len(trust_ids)} trusts.")


def unstage_project_service(project_id: UUID, current_user_id: UUID, session: Session) -> None:
    """
    Unstages a project by removing entries from ProjectTrustIntersect and updating status.

    Args:
        project_id (UUID): The ID of the project to unstage.
        current_user_id (UUID): The ID of the user performing the unstage action.
        session (Session): The SQLModel session to use for database operations.

    Returns:
        None

    Raises:
        ValueError: If the project does not exist or is deleted.
    """
    # Check if project exists and is not deleted
    project = session.get(Projects, project_id)
    if not project or project.deleted:
        raise ValueError(f"Project {project_id} does not exist or is deleted, cannot unstage.")

    stmt_delete = delete(ProjectTrustIntersect).where(col(ProjectTrustIntersect.project_id) == project_id)
    result = session.execute(stmt_delete)
    deleted_count = getattr(result, "rowcount", 0) or 0

    if deleted_count == 0:
        logger.warn(
            f"No rows deleted from ProjectTrustIntersect for project {project_id} during unstage. Project might not "
            "have been staged."
        )

    # Update project status and add audit entry
    update_project_status(project_id=project_id, new_status=ProjectStatus.UNSTAGED, session=session)
    audit_project_action(
        project_id=project_id,
        action=ProjectAuditAction.UNSTAGE,
        user_id=current_user_id,
        session=session,
    )
    # Commit the transaction
    session.commit()

    logger.info(f"Project {project_id} unstaged by user {current_user_id}.")


def get_reimport_queries_service(max_reimport_count: int, session: Session) -> List[IReimportQuery]:
    """
    Fetch queries eligible for reimport, using SQLModel-style selects of models.
    Assumes relationships:
    - XNATProjectStatus.trust -> Trust
    - (optional) Queries -> XNATProjectStatus via project_id
    """
    try:
        stmt = (
            select(Queries, XNATProjectStatus, Trust)
            .join(XNATProjectStatus, col(Queries.project_id) == col(XNATProjectStatus.project_id))
            .join(Trust, col(XNATProjectStatus.trust_id) == Trust.id)
            .where(
                XNATProjectStatus.reimport_count < max_reimport_count,
                XNATProjectStatus.retrieve_image_status != XNATImageStatus.DELETED,
                XNATProjectStatus.query_at_creation == Queries.id,
            )
        )

        rows = session.exec(stmt).all()  # List[tuple[Queries, XNATProjectStatus, Trust]]

        return [
            IReimportQuery(
                query_id=q.id,
                query=q.query,
                xnat_project_id=xps.xnat_project_id,
                last_reimport=xps.last_reimport,
                trust_id=t.id,
                trust_endpoint=t.endpoint,
                trust_name=t.name,
            )
            for (q, xps, t) in rows
        ]

    except Exception as e:
        error_message = f"Error fetching reimport queries: {e}"
        logger.error(error_message)
        raise ValueError(error_message)


def get_project(project_id: UUID, session: Session) -> IProjectResponse:
    """
    Retrieves project details along with the most recent query information.

    Args:
        project_id (UUID): The ID of the project to retrieve.
        session (Session): The SQLModel session to use for database operations.

    Returns:
        IProjectResponse: An object containing project details and the most recent query information if available.

    Raises:
        HTTPException: If the request cannot be processed.
    """
    logger.debug(f"Attempting to get project details for ID: {project_id}")

    # Step 1: Fetch the project
    project = session.exec(
        select(Projects).where(
            Projects.id == project_id,
            col(Projects.deleted).is_(False),
        )
    ).first()

    if not project:
        logger.warning(f"No project found with ID: {project_id}")
        raise HTTPException(
            status_code=404,
            detail=f"Project with ID {project_id} not found or deleted.",
        )

    logger.debug(f"Project found: {project}")

    # Step 2: Fetch most recent query (by created timestamp)
    query = session.exec(
        select(Queries).where(Queries.project_id == project.id).order_by(col(Queries.created).desc()).limit(1)
    ).first()

    query_data = None
    if query:
        # Step 3: Count distinct trusts in QueryResult
        trust_count = (
            session.exec(
                select(func.count(func.distinct(QueryResult.trust_id))).where(QueryResult.query_id == query.id)
            ).first()
            or 0
        )

        # Step 4: Get total cohort size from QueryStats (as JSON)
        stats_entry = session.exec(select(QueryStats).where(QueryStats.query_id == query.id)).first()

        total_cohort = 0
        if stats_entry:
            try:
                stats_json = json.loads(stats_entry.stats)
                total_cohort = int(stats_json.get("TotalCount") or stats_json.get("record_count") or 0)
            except (ValueError, TypeError) as e:
                logger.warning(f"Failed to parse stats JSON for query {query.id}: {e}")

        query_data = IProjectQuery(
            id=query.id,
            name=query.name,
            query=query.query,
            trusts_queried=trust_count,
            total_cohort=total_cohort,
        )  # type: ignore[call-arg]

    # Step 5: Construct response
    project_response = IProjectResponse(
        id=project.id,
        name=project.name,
        description=project.description,
        deleted=project.deleted,
        owner_id=project.owner_id,
        creation_timestamp=project.creation_timestamp,
        status=project.status,
        query=query_data,
    )  # type: ignore[call-arg]

    logger.debug(f"Returning project response: {project_response}")
    return project_response


def get_users_with_access(project_id: UUID, session: Session) -> List[UUID]:
    """
    Retrieves a list of user IDs who have access to a specific project.

    Args:
        project_id (UUID): The ID of the project.
        session (Session): The SQLModel session to use for database operations.

    Returns:
        List[UUID]: A list of user IDs with access to the project.
    """
    logger.debug("Attempting to get project users...")

    statement = select(ProjectUserAccess.user_id).where(ProjectUserAccess.project_id == project_id)
    result = session.exec(statement).all()

    return list(result) if result else []
