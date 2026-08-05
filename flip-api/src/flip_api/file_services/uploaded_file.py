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

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlmodel import Session, select

from flip_api.auth.access_manager import can_modify_model
from flip_api.auth.dependencies import verify_token
from flip_api.config import get_settings
from flip_api.db.database import get_session
from flip_api.db.models.main_models import UploadedFiles
from flip_api.domain.schemas.file import FileUploadStatus, SafeFileName
from flip_api.file_services.services.malware_scan_service import reconcile_uploaded_file_in_background
from flip_api.utils.logger import logger
from flip_api.utils.s3_client import S3Client

router = APIRouter(prefix="/files", tags=["file_services"])


@router.post("/process-scanned-file/{model_id}/{file}", response_model=dict[str, str])
def process_scanned_file(
    model_id: UUID,
    file: SafeFileName,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
) -> dict[str, str]:
    """
    Register an uploaded model file and kick off its malware-scan reconcile.

    Called by the UI once the presigned POST to the staging prefix succeeds.
    Upserts the file's DB row as ``SCANNING`` (re-registering a file resets
    any earlier terminal status — that is the only path out of ``INFECTED`` /
    ``ERROR``), then schedules ``reconcile_uploaded_file`` as a background
    task so the response never blocks on a download-and-scan. The UI observes
    the outcome (``COMPLETED`` / ``INFECTED`` / ``ERROR``) through its normal
    model polling; rows orphaned by a restart are recovered by the scheduled
    sweep.

    Args:
        model_id: The ID of the model the file belongs to
        file: The name of the uploaded file
        background_tasks: FastAPI background-task hook the reconcile runs on
        db: Database session
        user_id: ID of the user making the request, obtained from authentication

    Returns:
        dict[str, str]: ``message`` plus the row's current ``status``
        (always ``SCANNING`` on success).

    Raises:
        HTTPException: 403 if the caller is not allowed to modify the model,
            404 if the file is not present in the staging prefix, or 500 on
            an unexpected failure.
    """
    try:
        if not can_modify_model(user_id, model_id, db):
            logger.error(f"User ID: {user_id} is not allowed to modify model {model_id}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"User with ID: {user_id} is not allowed to modify files for this model",
            )

        s3 = S3Client()
        uploaded_path = f"{get_settings().UPLOADED_MODEL_FILES_BUCKET}/{model_id}/{file}"

        if not s3.object_exists(uploaded_path):
            logger.error(f"File {file!r} not found in the staging prefix for model {model_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"File {file} has not been uploaded for this model",
            )

        head_object = s3.head_object(uploaded_path)
        content_length = head_object.get("ContentLength", 0)
        content_type = head_object.get("ContentType", "application/octet-stream")

        db_file = db.exec(
            select(UploadedFiles).where(UploadedFiles.name == file, UploadedFiles.model_id == model_id)
        ).first()

        now = datetime.now(timezone.utc)
        if db_file:
            db_file.status = FileUploadStatus.SCANNING
            db_file.size = content_length
            db_file.type = content_type
            db_file.updated_at = now
        else:
            db_file = UploadedFiles(
                name=file,
                status=FileUploadStatus.SCANNING,
                size=content_length,
                type=content_type,
                model_id=model_id,
                updated_at=now,
            )
            db.add(db_file)
        db.commit()
        logger.info(
            f"Registered file for scanning. Name: {file}, size: {content_length}, "
            f"type: {content_type}, model ID: {model_id}"
        )

        background_tasks.add_task(reconcile_uploaded_file_in_background, model_id, file)

        return {"message": "File registered for scanning", "status": FileUploadStatus.SCANNING.value}

    except HTTPException:
        raise
    except Exception as e:
        # Log the class name only — never echo exception text into the
        # response, and keep URL-bearing messages out of the log line.
        logger.error(f"Unhandled error in process_scanned_file for model {model_id}: {type(e).__name__}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        )
