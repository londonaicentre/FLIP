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

from flip_api.auth.auth_utils import has_permissions
from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.db.models.user_models import PermissionRef
from flip_api.domain.interfaces.model import IAllModelsResponse
from flip_api.domain.schemas.status import ModelStatus
from flip_api.model_services.services.model_service import get_all_models_service
from flip_api.utils.logger import logger
from flip_api.utils.paging_utils import IPagedData, get_total_pages

router = APIRouter(prefix="/models", tags=["model_services"])


def _parse_status(raw_status: str | None) -> ModelStatus | None:
    """Validate the optional ``status`` query param against ``ModelStatus``.

    Args:
        raw_status (str | None): The raw ``status`` query param, if supplied.

    Returns:
        ModelStatus | None: The parsed status, or None when no filter was requested.

    Raises:
        HTTPException: 400 if a non-empty value does not match a known status.
    """
    if not raw_status:
        return None
    try:
        return ModelStatus(raw_status)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid model status filter: {raw_status}",
        )


@router.get(
    "",
    summary="Get a paginated, access-scoped list of models across every project.",
    response_model=IPagedData[IAllModelsResponse],
    status_code=status.HTTP_200_OK,
)
def get_all_models_endpoint(
    request: Request,
    session: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
) -> IPagedData[IAllModelsResponse]:
    """Estate-wide model list, scoped to the projects the caller can access (issue #726).

    A caller sees models for projects they own or have been granted access to; a user with
    ``CAN_MANAGE_PROJECTS`` sees every model. Supports search (model or project name), an
    optional ``status`` filter and pagination.

    Args:
        request (Request): The HTTP request, used to read query params.
        session (Session): The database session.
        user_id (UUID): The authenticated caller's id.

    Returns:
        IPagedData[IAllModelsResponse]: A paginated page of models joined with project, owner and trusts.
    """
    logger.info("Requesting estate-wide models list")

    query_params = dict(request.query_params)
    status_filter = _parse_status(query_params.get("status"))

    # Managers see every model — drop the per-user access filter (equivalent to user_id = None),
    # mirroring get_projects_endpoint.
    if has_permissions(user_id, [PermissionRef.CAN_MANAGE_PROJECTS], session):
        requesting_user_id = None
    else:
        requesting_user_id = user_id

    response, paging = get_all_models_service(session, requesting_user_id, query_params, status_filter)
    total_pages = get_total_pages(response.total_rows, paging.page_size)

    return IPagedData(
        page=paging.page_number,
        page_size=paging.page_size,
        total_pages=total_pages,
        total_records=response.total_rows,
        data=response.data,
    )  # type: ignore[call-arg]
