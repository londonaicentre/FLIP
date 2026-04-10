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

from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select

from flip_api.auth.access_manager import can_access_model
from flip_api.auth.dependencies import verify_token
from flip_api.config import get_settings
from flip_api.db.database import get_session
from flip_api.db.models.main_models import UploadedFiles
from flip_api.utils.logger import logger
from flip_api.utils.s3_client import S3Client

router = APIRouter(prefix="/files", tags=["file_services"])


# [#114] ✅
@router.get("/model/{model_id}/{file_name}", response_class=StreamingResponse, response_model=None)
def download_file(
    model_id: UUID,
    file_name: str,
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
) -> StreamingResponse:
    """
    Download a model file from S3.

    Args:
        model_id (UUID): The ID of the model to retrieve the file for.
        file_name (str): The name of the file to download.
        db (Session): Database session.
        user_id (UUID): User ID from authentication.

    Returns:
        StreamingResponse: A streaming response containing the file content.

    Raises:
        HTTPException: If the user does not have access to the model, if the file does not exist, or there is an error
                       during the download process.
    """
    try:
        # Check user access
        if not can_access_model(user_id, model_id, db):
            logger.error(f"User ID: {user_id} does not have access to Model ID: {model_id}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"User with ID: {user_id} is denied access to this model",
            )

        # Check if file exists in database
        db_file = db.exec(
            select(UploadedFiles).where(UploadedFiles.model_id == model_id, UploadedFiles.name == file_name)
        ).first()

        if not db_file:
            logger.error(f"File {file_name} not found for Model ID: {model_id} in the database.")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"File {file_name} not found for Model ID: {model_id}",
            )

        # Get file from S3
        s3_path = f"{get_settings().SCANNED_MODEL_FILES_BUCKET}/{model_id}/{file_name}"
        logger.debug(f"Downloading file {file_name} from {s3_path}")

        s3 = S3Client()
        try:
            s3_response = s3.get_object(s3_path)
            body = s3_response["Body"]
            content_type = s3_response.get("ContentType", "application/octet-stream")
            logger.debug(f"s3_response {s3_response.keys()}")
            logger.debug(f"File {file_name} downloaded successfully from S3.")

            # Return file as response
            disposition = f"attachment; filename=\"{file_name}\"; filename*=UTF-8''{quote(file_name)}"

            return StreamingResponse(
                body,
                media_type=content_type,
                headers={
                    "Content-Disposition": disposition,
                    # Optional but often helpful:
                    # "Cache-Control": "no-store",
                },
            )
        except Exception as e:
            error_message = f"Error downloading {s3_path} from S3: {e}"
            logger.error(error_message)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=error_message,
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unhandled error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {e}",
        )
