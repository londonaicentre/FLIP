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
from sqlmodel import Session, select

from flip_api.auth.access_manager import can_access_model
from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.db.models.main_models import UploadedFiles
from flip_api.utils.logger import logger

router = APIRouter(prefix="/files", tags=["file_services"])


# TODO [#114] This endpoint was not defined in the old repo.
@router.get("/model/{model_id}/get/files", response_model=list[dict[str, Any]])
def get_model_files_list(
    model_id: UUID,
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
) -> list[dict[str, Any]]:
    """
    Get list of files for a specific model.

    Args:
        model_id (UUID): The ID of the model to retrieve files for.
        db (Session): Database session.
        user_id (UUID): ID of the user (obtained from auth token).

    Returns:
        List[Dict[str, Any]]: A list of dictionaries containing file information.

    Raises:
        HTTPException: If the user does not have access to the model or if there is an error during the operation.
    """
    try:
        # Check user access
        if not can_access_model(user_id, model_id, db):
            logger.error(f"User ID: {user_id} does not have access to Model ID: {model_id}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"User with ID: {user_id} is denied access to this model",
            )

        # Query files for model
        files = db.exec(select(UploadedFiles).where(UploadedFiles.model_id == model_id)).all()
        logger.info(f"Files for model {model_id}: {files}")

        # Format response
        result = []
        for file in files:
            result.append({
                "id": str(file.id) if file.id else None,
                "name": file.name,
                "size": file.size,
                "type": file.type,
                "status": file.status,
                "modelId": str(model_id),
                "created": file.created.isoformat() if hasattr(file, "created") and file.created else None,
                "modified": file.modified.isoformat() if hasattr(file, "modified") and file.modified else None,
            })

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unhandled error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {e}",
        )
