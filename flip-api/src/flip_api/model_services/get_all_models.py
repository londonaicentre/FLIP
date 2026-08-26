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
from sqlmodel import Session

from flip_api.auth.access_manager import can_access_project
from flip_api.auth.auth_utils import has_permissions
from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.db.models.user_models import PermissionRef
from flip_api.domain.interfaces.model import IModelsListResponse, IModelsProjectOption
from flip_api.domain.schemas.status import ModelStatus
from flip_api.model_services.services.model_service import (
    get_all_models_service,
    get_models_project_options_service,
)
from flip_api.utils.logger import logger
from flip_api.utils.paging_utils import get_total_pages
from flip_api.utils.project_manager import get_project_by_id

router = APIRouter(prefix="/models", tags=["model_services"])


def _parse_statuses(raw_status: str | None) -> list[ModelStatus] | None:
    """Validate the optional ``status`` query param (comma-separated) against ``ModelStatus``.

    A comma-separated list lets the group filter tiles narrow to several statuses at once
    (e.g. ``INITIATED,PENDING`` for "Queued").

    Args:
        raw_status (str | None): The raw ``status`` query param, if supplied.

    Returns:
        list[ModelStatus] | None: The parsed statuses, or None when no filter was requested.

    Raises:
        HTTPException: 400 if any value does not match a known status.
    """
    if not raw_status:
        return None
    parsed: list[ModelStatus] = []
    for part in raw_status.split(","):
        value = part.strip()
        if not value:
            continue
        try:
            parsed.append(ModelStatus(value))
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid model status filter: {value}",
            )
    return parsed or None


def _parse_project_id(raw_project: str | None) -> UUID | None:
    """Validate the optional ``project`` query param, which scopes the list to one project.

    Parsed explicitly rather than passed through: ``get_paging_details`` silently ignores query
    keys it does not recognise, so a malformed id would otherwise degrade to "no filter" and hand
    back the whole estate under a UI that claims to be showing one project.

    Args:
        raw_project (str | None): The raw ``project`` query param, if supplied.

    Returns:
        UUID | None: The parsed project id, or None when no filter was requested.

    Raises:
        HTTPException: 400 if the value is not a UUID.
    """
    if not raw_project or not raw_project.strip():
        return None
    try:
        return UUID(raw_project.strip())
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid project filter: {raw_project}",
        )


@router.get(
    "/projects",
    summary="List the projects the caller can access, for the Models page's project filter.",
    response_model=list[IModelsProjectOption],
    status_code=status.HTTP_200_OK,
)
def get_models_project_options_endpoint(
    session: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
) -> list[IModelsProjectOption]:
    """Options for the Models page's project filter — every project the caller can access.

    Unpaginated and deliberately thin (id, name, status): the dropdown must list *all* accessible
    projects, which one page of the paginated ``/projects`` endpoint cannot promise, and it has no
    use for that endpoint's trusts, cohort query or user rows.

    Args:
        session (Session): The database session.
        user_id (UUID): The authenticated caller's id.

    Returns:
        list[IModelsProjectOption]: Accessible projects, ordered by name.
    """
    # Managers filter by any project — drop the per-user access filter, as the list route does.
    if has_permissions(user_id, [PermissionRef.CAN_MANAGE_PROJECTS], session):
        requesting_user_id = None
    else:
        requesting_user_id = user_id

    return get_models_project_options_service(session, requesting_user_id)


@router.get(
    "",
    summary="Get a paginated, access-scoped list of models across every project.",
    response_model=IModelsListResponse,
    status_code=status.HTTP_200_OK,
)
def get_all_models_endpoint(
    request: Request,
    session: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
) -> IModelsListResponse:
    """Estate-wide model list, scoped to the projects the caller can access (issue #726).

    A caller sees models for projects they own or have been granted access to; a user with
    ``CAN_MANAGE_PROJECTS`` sees every model. Supports search (model or project name), a
    comma-separated ``status`` filter, a single-``project`` filter and pagination, and returns
    per-status tile counts. The project filter scopes those counts too, so the UI's filter tiles
    describe the project in view rather than the estate.

    Args:
        request (Request): The HTTP request, used to read query params.
        session (Session): The database session.
        user_id (UUID): The authenticated caller's id.

    Returns:
        IModelsListResponse: A page of models (project, owner, trusts) plus per-status counts.
    """
    logger.info("Requesting estate-wide models list")

    query_params = dict(request.query_params)
    status_filter = _parse_statuses(query_params.get("status"))
    project_filter = _parse_project_id(query_params.get("project"))

    # Managers see every model — drop the per-user access filter (equivalent to user_id = None),
    # mirroring get_projects_endpoint.
    if has_permissions(user_id, [PermissionRef.CAN_MANAGE_PROJECTS], session):
        requesting_user_id = None
    else:
        requesting_user_id = user_id

    if project_filter is not None:
        # Access is checked BEFORE existence, deliberately: a non-manager gets the same 403 whether
        # the project is merely not theirs or not there at all, so this route never confirms to a
        # caller probing ids whether a guessed project exists. The 404 is reachable only by
        # CAN_MANAGE_PROJECTS holders — who can list every project anyway, and whose stale link to
        # a deleted project should say so. Leaving both to the access predicate would instead
        # return an empty page, which reads as "this project has no models" — indistinguishable
        # from "not yours", and baffling on a shared link.
        # requesting_user_id is None exactly when the caller holds CAN_MANAGE_PROJECTS (computed
        # above), for whom can_access_project short-circuits True — skip the duplicate lookup.
        if requesting_user_id is not None and not can_access_project(user_id, project_filter, session):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this project",
            )
        if get_project_by_id(project_filter, session) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Project {project_filter} not found",
            )

    response, status_counts, paging = get_all_models_service(
        session, requesting_user_id, query_params, status_filter, project_filter
    )
    total_pages = get_total_pages(response.total_rows, paging.page_size)

    return IModelsListResponse(
        page=paging.page_number,
        page_size=paging.page_size,
        total_pages=total_pages,
        total_records=response.total_rows,
        data=response.data,
        status_counts=status_counts,
    )  # type: ignore[call-arg]
