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
from sqlmodel import Session, col, select

from flip_api.auth.dependencies import verify_token
from flip.config import get_settings
from flip.db.database import get_session
from flip.db.models.main_models import Model, Projects
from flip.domain.schemas.file import UploadFileBody
from flip.utils.logger import logger
from flip.utils.s3_client import S3Client

router = APIRouter(prefix="/files", tags=["file_services"])


# [#114] ✅
@router.post("/preSignedUrl/model/{model_id}")
def get_presigned_url_for_upload(
    model_id: str,
    body: UploadFileBody,
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
) -> str:
    """
    Generate a pre-signed URL for uploading model files to S3.

    Args:
        model_id (str): The ID of the model to which the file will be uploaded.
        body (UploadFileBody): The request body containing the file name.
        db (Session): Database session dependency.

    Returns:
        str: A pre-signed URL for uploading the file to S3.

    Raises:
        HTTPException: If the model does not exist or is marked as deleted,
                       or if there is an error generating the pre-signed URL.
    """
    try:
        # TODO: Implement user authentication if needed
        # Check if user can access the model via your access logic

        # Check if model exists and isn't deleted
        statement = (
            select(Model)
            .join(Projects)
            .where(
                Model.id == model_id,
                col(Projects.deleted).is_(False),
                Projects.id == Model.project_id,
            )
        )
        result = db.exec(statement)
        existing_model = result.first()

        if not existing_model:
            logger.error(f"Model ID: {model_id} does not exist or is marked as deleted.")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Model ID: {model_id} does not exist or is deleted.",
            )

        # Check if a pre-signed URL override is provided via environment variable
        pre_signed_url = get_settings().PRE_SIGNED_URL
        if pre_signed_url:
            logger.info(f"Using environment variable override for pre-signed URL: {pre_signed_url}")
            return pre_signed_url

        # Generate a new pre-signed URL
        logger.info("No pre-signed URL override found. Generating a new pre-signed URL.")

        s3_path = f"{get_settings().UPLOADED_MODEL_FILES_BUCKET}/{model_id}/{body.fileName}"

        s3 = S3Client()
        try:
            pre_signed_url = s3.get_put_presigned_url(s3_path)
            logger.info(f"Generated pre-signed URL: {pre_signed_url}")
            return pre_signed_url
        except Exception as e:
            error_msg = f"Could not create a pre-signed URL for {s3_path}. Error: {str(e)}"
            logger.error(error_msg)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=error_msg,
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unhandled error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        )
