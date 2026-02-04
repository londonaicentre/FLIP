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

from typing import Any, Dict, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, col, select

from flip_api.auth.dependencies import verify_token
from flip.db.database import get_session
from flip.db.models.main_models import UploadedFiles
from flip.domain.schemas.file import IdList
from flip.utils.logger import logger

router = APIRouter(prefix="/files", tags=["file_services"])


# TODO Remove duplicate code in these endpoints


# [#114] ✅
@router.get("/{file_ids}")
def get_uploaded_files_info(
    file_ids: str,
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
) -> List[Dict[str, Any]]:
    """
    Get information about uploaded files based on a list of IDs.

    Args:
        file_ids (str): Comma-separated string of file IDs
        db (Session): Database session
        user_id (UUID): ID of the user (obtained from auth token)

    Returns:
        List of file information

    Raises:
        HTTPException: If no files are found or if there is an error during the operation.
    """
    try:
        # Parse and validate the ID list
        id_list = [UUID(id_str.strip()) for id_str in file_ids.split(",")]

        # Validate using Pydantic model
        IdList(ids=id_list)

        # Query files by ID
        files = db.exec(select(UploadedFiles).where(col(UploadedFiles.id).in_(id_list))).all()

        if not files:
            logger.error("No files found")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No files found",
            )

        # Track which IDs were found and log missing IDs
        retrieved_ids = [str(file.id) for file in files]

        for id_str in [str(id) for id in id_list]:
            if id_str not in retrieved_ids:
                logger.error(f"File with ID: {id_str} not found")

        # Format response
        result = []
        for file in files:
            result.append({
                "id": str(file.id) if file.id else None,
                "name": file.name,
                "status": file.status,
                "size": file.size,
                "type": file.type,
                "modelId": str(file.model_id) if file.model_id else None,
                "created": file.created.isoformat() if hasattr(file, "created") and file.created else None,
                "modified": file.modified.isoformat() if hasattr(file, "modified") and file.modified else None,
            })

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
@router.post("/")
def get_uploaded_files_info_post(
    id_list: IdList,
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
) -> List[Dict[str, Any]]:
    """
    Get information about uploaded files based on a list of IDs (POST method).

    Args:
        id_list (IdList): Pydantic model containing a list of file IDs
        db (Session): Database session
        user_id (UUID): ID of the user (obtained from auth token)

    Returns:
        List of file information

    Raises:
        HTTPException: If no files are found or if there is an error during the operation.
    """
    try:
        # Query files by ID
        files = db.exec(select(UploadedFiles).where(col(UploadedFiles.id).in_(id_list.ids))).all()

        if not files:
            logger.error("No files found")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No files found",
            )

        # Track which IDs were found and log missing IDs
        retrieved_ids = [str(file.id) for file in files]

        for id_str in [str(id) for id in id_list.ids]:
            if id_str not in retrieved_ids:
                logger.error(f"File with ID: {id_str} not found")

        # Format response
        result = []
        for file in files:
            result.append({
                "id": str(file.id) if file.id else None,
                "name": file.name,
                "status": file.status,
                "size": file.size,
                "type": file.type,
                "modelId": str(file.model_id) if file.model_id else None,
                "created": file.created.isoformat() if hasattr(file, "created") and file.created else None,
                "modified": file.modified.isoformat() if hasattr(file, "modified") and file.modified else None,
            })

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
