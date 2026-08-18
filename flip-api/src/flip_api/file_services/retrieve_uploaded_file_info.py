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

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, col, select

from flip_api.auth.access_manager import can_access_model
from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.db.models.main_models import UploadedFiles
from flip_api.domain.schemas.file import IdList
from flip_api.utils.logger import logger

router = APIRouter(prefix="/files", tags=["file_services"])


def _filter_files_by_access(
    files: list[UploadedFiles],
    user_id: UUID,
    db: Session,
) -> list[UploadedFiles]:
    """Filter files to those whose owning model the user can access.

    Files with no ``model_id`` are denied by default — they are not attached to
    any model and therefore have no scope under which a caller can claim access.
    Access decisions are memoised per ``model_id`` to avoid redundant DB lookups
    when several files share the same model.

    Args:
        files: Files retrieved from the database.
        user_id: Authenticated caller.
        db: Database session.

    Returns:
        The subset of ``files`` the caller is allowed to see.
    """
    access_cache: dict[UUID, bool] = {}
    accessible: list[UploadedFiles] = []
    for file in files:
        if file.model_id is None:
            logger.warning(f"Denying access to file {file.id}: no associated model_id.")
            continue
        if file.model_id not in access_cache:
            access_cache[file.model_id] = can_access_model(user_id, file.model_id, db)
        if access_cache[file.model_id]:
            accessible.append(file)
        else:
            logger.warning(
                f"User {user_id} denied access to file {file.id} (model {file.model_id})."
            )
    return accessible


def _serialize_files(files: list[UploadedFiles]) -> list[dict[str, Any]]:
    """Serialize file records into the API response shape."""
    return [
        {
            "id": str(file.id) if file.id else None,
            "name": file.name,
            "status": file.status,
            "size": file.size,
            "type": file.type,
            "modelId": str(file.model_id) if file.model_id else None,
            "created": file.created.isoformat() if hasattr(file, "created") and file.created else None,
            "modified": file.modified.isoformat() if hasattr(file, "modified") and file.modified else None,
            "bandit_findings": file.bandit_findings,
        }
        for file in files
    ]


# TODO Remove duplicate code in these endpoints


# [#114] ✅
@router.get("/{file_ids}", response_model=list[dict[str, Any]])
def get_uploaded_files_info(
    file_ids: str,
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
) -> list[dict[str, Any]]:
    """
    Get information about uploaded files based on a list of IDs.

    Only files belonging to a model the caller can access are returned. Files
    outside the caller's scope are filtered out — the response does not
    distinguish between "does not exist" and "not authorised", to avoid
    leaking the existence of files via metadata enumeration.

    Args:
        file_ids (str): Comma-separated string of file IDs
        db (Session): Database session
        user_id (UUID): ID of the user (obtained from auth token)

    Returns:
        list[dict[str, Any]]: A list of dictionaries containing file information

    Raises:
        HTTPException: If no accessible files are found or if there is an error during the operation.
    """
    try:
        # Parse and validate the ID list
        id_list = [UUID(id_str.strip()) for id_str in file_ids.split(",")]

        # Validate using Pydantic model
        IdList(ids=id_list)

        # Query files by ID
        files = db.exec(select(UploadedFiles).where(col(UploadedFiles.id).in_(id_list))).all()

        # Filter to files the caller is authorised to see
        accessible_files = _filter_files_by_access(list(files), user_id, db)

        if not accessible_files:
            logger.warning(f"No files accessible to user {user_id} for the requested IDs.")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No files found",
            )

        # Track which IDs were returned and log the rest
        retrieved_ids = {str(file.id) for file in accessible_files}
        for id_str in (str(i) for i in id_list):
            if id_str not in retrieved_ids:
                logger.info(f"File with ID: {id_str} not returned (missing or access denied)")

        result = _serialize_files(accessible_files)
        logger.info(f"Retrieved status for {len(result)} files")
        return result

    except ValueError as e:
        # Invalid UUID format
        logger.error(f"Invalid UUID format: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid UUID format: {str(e)}",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unhandled error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {e}",
        )


# TODO [#114] This endpoint was not defined in the old repo.
# Alternative implementation with a POST request and request body
@router.post("", response_model=list[dict[str, Any]])
def get_uploaded_files_info_post(
    id_list: IdList,
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
) -> list[dict[str, Any]]:
    """
    Get information about uploaded files based on a list of IDs (POST method).

    Only files belonging to a model the caller can access are returned. Files
    outside the caller's scope are filtered out — the response does not
    distinguish between "does not exist" and "not authorised", to avoid
    leaking the existence of files via metadata enumeration.

    Args:
        id_list (IdList): Pydantic model containing a list of file IDs
        db (Session): Database session
        user_id (UUID): ID of the user (obtained from auth token)

    Returns:
        list[dict[str, Any]]: A list of dictionaries containing file information

    Raises:
        HTTPException: If no accessible files are found or if there is an error during the operation.
    """
    try:
        # Query files by ID
        files = db.exec(select(UploadedFiles).where(col(UploadedFiles.id).in_(id_list.ids))).all()

        # Filter to files the caller is authorised to see
        accessible_files = _filter_files_by_access(list(files), user_id, db)

        if not accessible_files:
            logger.warning(f"No files accessible to user {user_id} for the requested IDs.")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No files found",
            )

        # Track which IDs were returned and log the rest
        retrieved_ids = {str(file.id) for file in accessible_files}
        for id_str in (str(i) for i in id_list.ids):
            if id_str not in retrieved_ids:
                logger.info(f"File with ID: {id_str} not returned (missing or access denied)")

        result = _serialize_files(accessible_files)
        logger.info(f"Retrieved status for {len(result)} files")
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unhandled error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {e}",
        )
