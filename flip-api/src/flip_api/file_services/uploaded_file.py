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

from flip_api.auth.dependencies import verify_token
from flip_api.config import get_settings
from flip_api.db.database import get_session
from flip_api.db.models.main_models import UploadedFiles
from flip_api.domain.schemas.file import FileUploadStatus
from flip_api.utils.logger import logger
from flip_api.utils.s3_client import S3Client

router = APIRouter(prefix="/files", tags=["file_services"])


# def map_bucket_status_to_file_status(bucket_status: BucketStatus) -> FileUploadStatus:
#     """Map bucket status to file upload status."""
#     if bucket_status == BucketStatus.CLEAN:
#         return FileUploadStatus.COMPLETED
#     else:
#         logger.info(f"Bucket status is neither clean or infected. Status: {bucket_status}")
#         return FileUploadStatus.ERROR


# def delete_file_from_s3(s3: S3Client, bucket: str, key: str) -> None:
#     """Delete file from S3 bucket and log result."""
#     s3.delete_object(bucket, key)
#     logger.info(f"Deleted {key} from {bucket}")


# TODO [#114] This endpoint was not defined in the old repo.
@router.post("/process-scanned-file/{model_id}/{file}")
def process_scanned_file(
    model_id: str,
    file: str,
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
):
    """
    Process a scanned file message from SNS.

    This endpoint receives SNS notifications about scanned files,
    updates the database, and manages the files in S3 buckets.
    """
    try:
        s3 = S3Client()

        file_status = FileUploadStatus.COMPLETED

        s3_path = f"{get_settings().UPLOADED_MODEL_FILES_BUCKET}/{model_id}/{file}"

        # Try to find the file in the uploaded model files bucket
        try:
            logger.debug(f"Checking if the file {file} exists in the uploaded model files bucket...")
            head_object = s3.head_object(s3_path)
            logger.info(f"File {file} exists in the uploaded model files bucket.")
            logger.info(
                f"Successfully retrieved the file size and type. "
                f"Size: {head_object.get('ContentLength')}, type: {head_object.get('ContentType')}"
            )
        except Exception:
            logger.error(f"File {file} does not exist in the uploaded model files bucket.")

        # Get file size and type
        # try:
        #     logger.debug("Attempting to retrieve file size and type from S3...")
        #     head_object = s3.head_object(s3_path)
        #     logger.info(
        #         f"Successfully retrieved the file size and type. "
        #         f"Size: {head_object.get('ContentLength')}, type: {head_object.get('ContentType')}"
        #     )
        # except Exception:
        #     logger.error(f"Unable to retrieve the file's details from s3: {s3_path}")
        #     raise HTTPException(
        #         status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        #         detail="Unable to retrieve the file's details",
        #     )

        # Extract model ID from key
        logger.debug("Attempting to extract model ID from the file's key...")
        # key_parts = file.split("/")
        # model_id = key_parts[0]
        file_name = file
        logger.debug(f"Extracted model ID: {model_id}, file name: {file_name} from the file's key: {file}")

        # Validate model ID (simplified validation)
        if not model_id:
            logger.error("Invalid model ID")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File does not belong to a model with a valid model ID",
            )

        logger.info(f"Extracted the model ID from the file's key successfully. Model ID: {model_id}")

        # Insert or update file in database
        logger.debug("Attempting to add file to database...")

        content_length = head_object.get("ContentLength", 0)
        content_type = head_object.get("ContentType", "deleted")

        # Check if file exists in database
        db_file = db.exec(
            select(UploadedFiles).where(UploadedFiles.name == file_name, UploadedFiles.model_id == model_id)
        ).first()

        if db_file:
            # Update existing file
            db_file.status = file_status
            db_file.size = content_length
            db_file.type = content_type
        else:
            # Create new file record
            db_file = UploadedFiles(
                name=file_name, status=file_status.value, size=content_length, type=content_type, model_id=model_id
            )
            db.add(db_file)

        db.commit()
        logger.info(
            f"File has been added to the database. "
            f"Key: {file_name}, status: {file_status.value}, size: {content_length}, "
            f"type: {content_type}, model ID: {model_id}"
        )

        # If file is not clean, delete it and return error
        # if scanned_file_message.status != BucketStatus.CLEAN:
        #     logger.error(
        #         f"The file {scanned_file_message.key} does not have a status of 'clean'. {json.dumps(message)}"
        #     )

        #     try:
        #         delete_file_from_s3(s3, scanned_file_message.bucket, scanned_file_message.key)
        #     except Exception:
        #         logger.warning("Unable to delete scanned file in bucket. This will need clearing up manually.")

        #     raise HTTPException(
        #         status_code=status.HTTP_400_BAD_REQUEST,
        #         detail=f"The file {scanned_file_message.key} does not have a scanned status of 'clean'",
        #     )

        # Copy file to secure bucket
        # scanned_model_files_bucket = get_settings().SCANNED_MODEL_FILES_BUCKET

        # try:
        #     s3.copy_object(
        #         scanned_file_message.bucket,
        #         scanned_file_message.key,
        #         scanned_model_files_bucket,
        #         scanned_file_message.key,
        #     )
        #     logger.info(f"Successfully copied {scanned_file_message.key} to {scanned_model_files_bucket}")
        # except Exception:
        #     logger.error(
        #         f"Unable to copy scanned file in secure bucket: "
        #         f"{scanned_file_message.bucket}/{scanned_file_message.key}"
        #     )
        #     raise HTTPException(
        #         status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        #         detail="Unable to copy scanned file in secure bucket",
        #     )

        # Verify file is accessible in secure bucket
        # try:
        #     logger.debug(f"Checking whether the file is accessible in the secure bucket {scanned_model_files_bucket}")
        #     # s3.head_object(scanned_model_files_bucket, scanned_file_message.key)
        #     # logger.info(f"Able to access the file {scanned_file_message.key} in {scanned_model_files_bucket}")
        # except Exception:
        #     # logger.error(
        #     #     f"Unable to access scanned file in secure bucket: "
        #     #     f"{scanned_model_files_bucket}/{scanned_file_message.key}"
        #     # )
        #     raise HTTPException(
        #         status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        #         detail="Unable to access scanned file in secure bucket",
        #     )

        # Delete file from original bucket
        # try:
        #     logger.debug(
        #         f"Attempting to remove file {scanned_file_message.key} from "
        #         "original bucket {scanned_file_message.bucket}"
        #     )
        #     delete_file_from_s3(s3, scanned_file_message.bucket, scanned_file_message.key)
        # except Exception:
        #     logger.warning("Unable to delete scanned file in original bucket. This will need clearing up manually.")

        # logger.info(f"Output: {json.dumps(message)}")

        # return message
        return {"File processed successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unhandled error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {e}",
        )
