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

from flip_api.auth.access_manager import can_modify_model
from flip_api.auth.dependencies import verify_token
from flip_api.config import get_settings
from flip_api.db.database import get_session
from flip_api.db.models.main_models import Model, Projects
from flip_api.domain.schemas.file import UploadFileBody
from flip_api.utils.logger import logger
from flip_api.utils.s3_client import MAX_PUT_PRESIGNED_URL_TTL_SECONDS, S3Client, hash_s3_key, parse_s3_path

router = APIRouter(prefix="/files", tags=["file_services"])


# [#114] ✅
@router.post("/preSignedUrl/model/{model_id}", response_model=str)
def get_presigned_url_for_upload(
    model_id: UUID,
    body: UploadFileBody,
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
) -> str:
    """
    Generate a pre-signed URL for uploading model files to S3.

    Args:
        model_id (UUID): The ID of the model to which the file will be uploaded.
        body (UploadFileBody): The request body containing the file name.
        db (Session): Database session dependency.
        user_id (UUID): ID of the authenticated user.

    Returns:
        str: A pre-signed URL for uploading the file to S3.

    Raises:
        HTTPException: If the user is not allowed, if the model does not exist or is marked as deleted,
                       or if there is an error generating the pre-signed URL.
    """
    try:
        # TODO: Implement user authentication if needed
        # Check if user can access the model via your access logic

        if not can_modify_model(user_id, model_id, db):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"User with ID: {user_id} is not allowed to upload files to this model",
            )

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

        # Check if a pre-signed URL override is provided via environment variable.
        # Never log the override value: it may itself be a signed URL.
        pre_signed_url = get_settings().PRE_SIGNED_URL
        if pre_signed_url:
            logger.info("Using PRE_SIGNED_URL environment-variable override")
            return pre_signed_url

        s3_path = f"{get_settings().UPLOADED_MODEL_FILES_BUCKET}/{model_id}/{body.fileName}"
        bucket, key = parse_s3_path(s3_path)
        key_hash = hash_s3_key(key)
        logger.info(
            "Generating pre-signed PUT URL "
            f"bucket={bucket} key_hash={key_hash} model_id={model_id}"
        )

        s3 = S3Client()
        try:
            pre_signed_url = s3.get_put_presigned_url(s3_path, expiration=MAX_PUT_PRESIGNED_URL_TTL_SECONDS)
            return pre_signed_url
        except Exception as e:
            # Drop ``exc_info`` entirely: the formatter would otherwise emit
            # ``str(e)`` via the traceback, and a future exception type that
            # embeds the URL in its message would leak through that channel.
            # Log structured context plus the exception class name only.
            logger.error(
                f"Could not create a pre-signed URL for bucket={bucket} "
                f"key_hash={key_hash} error_type={type(e).__name__}"
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Could not create a pre-signed URL",
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unhandled error in get_presigned_url_for_upload "
            f"model_id={model_id} error_type={type(e).__name__}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        )
