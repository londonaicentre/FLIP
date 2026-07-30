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
from sqlmodel import Session, select

from flip_api.auth.access_manager import can_modify_model
from flip_api.auth.dependencies import verify_token
from flip_api.config import get_settings
from flip_api.db.database import get_session
from flip_api.db.models.main_models import UploadedFiles
from flip_api.domain.schemas.file import PresignedDownloadResponse, SafeFileName
from flip_api.utils.logger import logger
from flip_api.utils.s3_client import S3Client

router = APIRouter(prefix="/files", tags=["file_services"])


# [#114] ✅
@router.get("/model/{model_id}/{file_name}", response_model=PresignedDownloadResponse)
def download_file(
    model_id: UUID,
    file_name: SafeFileName,
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
) -> PresignedDownloadResponse:
    """
    Get a pre-signed download URL for a 'model file' (file uploaded by a user to train/evaluate a model).

    The file bytes are never proxied through flip-api: the caller fetches them directly from the returned
    S3 URL. This keeps large model-file downloads off a client-side request timeout (FLIP#784) and off the
    Fargate task's egress bandwidth.

    Important: Do not confuse with the 'retrieve_federated_results' endpoint which is for downloading 'model results'
    generated as a result of a training/evaluation job and are stored in a different S3 bucket. While 'viewer' users
    can download 'model results' using the 'retrieve_federated_results' endpoint, only users with 'modify' access to a
    model can download 'model files' using this 'download_file' endpoint.

    Args:
        model_id (UUID): The ID of the model to retrieve the file for.
        file_name (str): The name of the file to download.
        db (Session): Database session.
        user_id (UUID): User ID from authentication.

    Returns:
        PresignedDownloadResponse: A short-lived pre-signed S3 GET URL plus the file name.

    Raises:
        HTTPException: If the user is not allowed, if the file does not exist, or there is an error
                       generating the pre-signed URL.
    """
    try:
        # Check user access
        if not can_modify_model(user_id, model_id, db):
            logger.error(f"User ID: {user_id} is not allowed to modify model {model_id}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"User with ID: {user_id} is not allowed to download files from this model",
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

        # Generate a pre-signed URL for the file in S3
        s3_path = f"{get_settings().SCANNED_MODEL_FILES_BUCKET}/{model_id}/{file_name}"
        logger.debug(f"Generating pre-signed download URL for {file_name} at {s3_path}")

        s3 = S3Client()
        try:
            disposition = f"attachment; filename=\"{file_name}\"; filename*=UTF-8''{quote(file_name)}"
            url = s3.get_presigned_url(
                s3_path,
                expiration=get_settings().PRE_SIGNED_URL_EXPIRATION_SECONDS,
                response_content_disposition=disposition,
            )
            logger.debug(f"Pre-signed download URL generated successfully for {file_name}.")

            # Never log the URL itself: it carries X-Amz-Signature /
            # X-Amz-Credential, a readable capability against the bucket.
            return PresignedDownloadResponse(url=url, fileName=file_name)
        except Exception:
            logger.exception(f"Error generating pre-signed download URL for model_id={model_id}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Error downloading file",
            )

    except HTTPException:
        raise
    except Exception:
        logger.exception(f"Unhandled error in download_file for model_id={model_id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        )
