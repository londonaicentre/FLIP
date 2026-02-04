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
from sqlmodel import Session

from flip_api.auth.access_manager import can_access_model
from flip.auth.dependencies import verify_token
from flip.config import get_settings
from flip.db.database import get_session
from flip.domain.schemas.file import ModelFiles, ModelFilesList
from flip.utils.logger import logger
from flip.utils.s3_client import S3Client

router = APIRouter(prefix="/files", tags=["file_services"])


# TODO [#114] This endpoint was not defined in the old repo, rather, it was run as one step in a step function to
# update the model status 'initialiseEnvironment'.
@router.get("/model/{model_id}/files/list", response_model=ModelFilesList)
def retrieve_model_files_list(
    model_id: UUID,
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
) -> ModelFilesList:
    """
    Retrieve a list of model files from S3.

    Args:
        model_id (UUID): The ID of the model to retrieve files for.
        db (Session): Database session.
        user_id (UUID): User ID from authentication.

    Returns:
        ModelFilesList: A list of model files categorized by type (algo, opener, model).

    Raises:
        HTTPException: If the user does not have access to the model, if the S3 bucket is not defined, or if there are
                       no objects found for the specified model ID.
    """
    try:
        # Check user access
        if not can_access_model(user_id, model_id, db):
            logger.error(f"User ID: {user_id} does not have access to Model ID: {model_id}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"User with ID: {user_id} is denied access to this model",
            )

        # Get S3 bucket name from environment
        s3_path = f"{get_settings().SCANNED_MODEL_FILES_BUCKET}/{model_id}"

        # Initialize S3 client
        s3 = S3Client()

        # List objects in bucket with model_id prefix
        try:
            list_objects = s3.list_objects(s3_path)
            if not list_objects:
                error_message = f"No objects found in S3 path: {s3_path}"
                logger.error(error_message)
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=error_message,
                )
        except Exception as e:
            logger.error(f"An error occurred when finding the result data: {s3_path}. Error: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An error occurred when finding the result data",
            )

        logger.debug(f"List of files returned: {list_objects}")

        # Categorize files based on suffix
        model_files = ModelFiles()

        for file in list_objects:
            if file.endswith("monaialgo.py"):
                model_files.algo = file
            elif file.endswith("monaiopener.py"):
                model_files.opener = file
            elif file.endswith("monai-test.pth.tar"):
                model_files.model = file

        result = ModelFilesList(files=model_files)

        logger.info(f"Output: {result.model_dump()}")
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unhandled error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {e}",
        )
