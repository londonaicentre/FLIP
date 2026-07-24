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

from flip_api.auth.access_manager import can_access_model
from flip_api.auth.dependencies import verify_token
from flip_api.config import get_settings
from flip_api.db.database import get_session
from flip_api.db.models.main_models import Model, Projects
from flip_api.utils.logger import logger
from flip_api.utils.s3_client import S3Client

router = APIRouter(prefix="/files", tags=["file_services"])


# [#114] ✅
@router.get("/model/{model_id}/fl/results", response_model=list[str])
def retrieve_federated_results(
    model_id: UUID,
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
) -> list[str]:
    """
    Retrieve federated results for a model from S3.

    Args:
        model_id (UUID): The ID of the model to retrieve results for.
        db (Session): Database session.
        user_id (UUID): User ID from authentication.

    Returns:
        list[str]: A list of presigned URLs for the files associated with the model.

    Raises:
        HTTPException: If the user is not allowed, if the model ID does not exist, if S3 command
                       gives an error while listing objects, or if there are any errors retrieving objects from S3.
    """
    try:
        # Check user access
        if not can_access_model(user_id, model_id, db):
            logger.error(f"User ID: {user_id} does not have access to model {model_id}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"User with ID: {user_id} is denied access to this model",
            )

        # Check if model exists
        model_exists = db.exec(
            select(Model)
            .join(Projects, col(Model.project_id) == col(Projects.id))
            .where(
                Model.id == model_id,
                col(Projects.deleted).is_(False),
            )
        ).first()

        if not model_exists:
            logger.error(f"Model ID: {model_id} does not exist")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Model ID: {model_id} does not exist",
            )

        # Get S3 bucket name from environment
        s3_path = f"{get_settings().UPLOADED_FEDERATED_DATA_BUCKET}/{model_id}"

        # Initialize S3 client
        s3 = S3Client()

        # List objects in bucket with model_id prefix
        try:
            list_objects = s3.list_objects(s3_path)
        except Exception as e:
            # Drop ``exc_info``: the formatter would otherwise emit ``str(e)``
            # via the traceback. A future boto error shape that embeds a URL
            # fragment in its message would leak through that channel.
            logger.error(
                f"An error occurred when finding the result data in {s3_path} "
                f"error_type={type(e).__name__}"
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An error occurred when finding the result data",
            )

        logger.debug(f"Found {len(list_objects)} federated-result objects under {s3_path}")

        # Check if any files were found
        if len(list_objects) == 0:
            logger.info(f"No result data was found: {s3_path}")
            return []

        # Get presigned URLs for each file
        try:
            result = [s3.get_presigned_url(f) for f in list_objects]
        except Exception as e:
            logger.error(
                f"Error generating pre-signed URLs for model {model_id} "
                f"error_type={type(e).__name__}"
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An error occurred when attempting to retrieve the files",
            )

        # Never log the URL list: each entry contains X-Amz-Signature /
        # X-Amz-Credential which together form a readable capability against
        # the federated-results bucket.
        logger.info(f"Returned {len(result)} pre-signed URLs for model {model_id}")
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unhandled error in retrieve_federated_results for model {model_id} "
            f"error_type={type(e).__name__}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        )
