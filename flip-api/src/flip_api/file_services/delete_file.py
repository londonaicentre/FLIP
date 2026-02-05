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

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from flip_api.auth.access_manager import can_access_model
from flip_api.auth.dependencies import verify_token
from flip_api.config import get_settings
from flip_api.db.database import get_session
from flip_api.db.models.main_models import UploadedFiles
from flip_api.domain.schemas.file import ModelFileDelete
from flip_api.utils.logger import logger
from flip_api.utils.s3_client import S3Client

router = APIRouter(prefix="/files", tags=["file_services"])


# [#114] ✅
@router.delete("/model/{model_id}/{file_name}")
def delete_model_file(
    model_id: UUID,
    file_name: str,
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
):
    """
    Delete a model file from S3 and the database.

    Args:
        model_id: ID of the model
        file_name: Name of the file to delete
        db: Database session
        user_id: ID of the user (obtained from auth token)

    Returns:
        Success message if the file was deleted successfully
    """
    try:
        # Validate input
        ModelFileDelete(model_id=model_id, fileName=file_name, user_id=user_id)

        # Check user access
        if not can_access_model(user_id, model_id, db):
            logger.error(f"User ID: {user_id} does not have access to Model ID: {model_id}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"User with ID: {user_id} is denied access to this model",
            )

        # Delete from database
        db_file = db.exec(
            select(UploadedFiles).where(UploadedFiles.model_id == model_id, UploadedFiles.name == file_name)
        ).first()
        if db_file:
            db.delete(db_file)
            db.commit()
        else:
            logger.warning(f"File {file_name} not found for Model ID: {model_id} in the database.")

        # Delete from S3
        s3_path = f"{get_settings().SCANNED_MODEL_FILES_BUCKET}/{model_id}/{file_name}"

        s3 = S3Client()
        try:
            s3.delete_object(s3_path)
        except Exception as e:
            error_message = f"Error deleting {s3_path} from S3: {e}"
            logger.error(error_message)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=error_message,
            )

        return {"message": f"File {file_name} deleted successfully from Model ID: {model_id}"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unhandled error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {e}",
        )
