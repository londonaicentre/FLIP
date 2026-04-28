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

import os
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, col, select

from flip_api.auth.access_manager import can_modify_model
from flip_api.auth.dependencies import verify_token
from flip_api.config import get_settings
from flip_api.db.database import get_session
from flip_api.db.models.main_models import Model, Projects, UploadedFiles
from flip_api.domain.schemas.file import UploadFileBody
from flip_api.domain.schemas.status import FileUploadStatus
from flip_api.utils.logger import logger
from flip_api.utils.s3_client import S3Client

router = APIRouter(prefix="/files", tags=["file_services"])


def _normalised_extension(file_name: str) -> str:
    """Return the lowercase extension of ``file_name`` without the leading dot."""
    return os.path.splitext(file_name)[1].lstrip(".").lower()


def _validate_upload_request(file_name: str, file_size: int | None) -> None:
    """Reject filenames whose extension is not whitelisted or whose declared size is too large.

    Extension and size are also re-checked once S3 confirms the upload, but
    rejecting upfront prevents wasted PUTs and surfaces a clear error to the
    caller. The size check here is advisory — a malicious caller can lie about
    ``file_size`` and the server-side check after upload is the authoritative
    enforcement point.
    """
    if not file_name or "/" in file_name or "\\" in file_name or "\x00" in file_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file name.",
        )

    settings = get_settings()
    allowed = {ext.lower() for ext in settings.ALLOWED_MODEL_FILE_EXTENSIONS}
    extension = _normalised_extension(file_name)
    if extension not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"File extension '{extension or '<none>'}' is not permitted. "
                f"Allowed extensions: {sorted(allowed)}."
            ),
        )

    if file_size is not None and file_size > settings.MAX_MODEL_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Declared file size {file_size} exceeds the maximum allowed "
                f"({settings.MAX_MODEL_FILE_SIZE_BYTES} bytes)."
            ),
        )


def _record_pending_upload(db: Session, model_id: UUID, file_name: str, file_size: int | None) -> None:
    """Insert or reset the ``UploadedFiles`` row for a model+filename to ``SCANNING``.

    A row is created at presigned-URL issue time so the UI can render a
    "scanning" state for the file before the malware scan completes. If a row
    already exists (e.g. user re-uploads a previously errored file) it is
    reset rather than duplicated.
    """
    existing = db.exec(
        select(UploadedFiles).where(UploadedFiles.model_id == model_id, UploadedFiles.name == file_name)
    ).first()
    if existing:
        existing.status = FileUploadStatus.SCANNING
        existing.size = float(file_size or 0)
        existing.type = ""
    else:
        db.add(
            UploadedFiles(
                name=file_name,
                status=FileUploadStatus.SCANNING,
                size=float(file_size or 0),
                type="",
                model_id=model_id,
            )
        )
    db.commit()


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

    The file is uploaded into the *staging* bucket (``UPLOADED_MODEL_FILES_BUCKET``).
    A malware scan runs asynchronously and emits an SNS notification consumed by
    the ``/files/process-scanned-file`` endpoint, which promotes clean files to
    ``SCANNED_MODEL_FILES_BUCKET`` (the canonical bucket downloads and FL clients
    read from) or deletes infected files.

    Args:
        model_id (UUID): The ID of the model to which the file will be uploaded.
        body (UploadFileBody): The request body containing the file name and optional size.
        db (Session): Database session dependency.
        user_id (UUID): ID of the authenticated user.

    Returns:
        str: A pre-signed URL for uploading the file to S3.

    Raises:
        HTTPException: 400 if the filename or size fails validation; 403 if the
            user cannot modify the model; 404 if the model does not exist or is
            deleted; 500 if URL generation fails.
    """
    try:
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

        _validate_upload_request(body.fileName, body.fileSize)

        # Check if a pre-signed URL override is provided via environment variable
        pre_signed_url = get_settings().PRE_SIGNED_URL
        if pre_signed_url:
            logger.info(f"Using environment variable override for pre-signed URL: {pre_signed_url}")
            _record_pending_upload(db, model_id, body.fileName, body.fileSize)
            return pre_signed_url

        # Generate a new pre-signed URL
        logger.info("No pre-signed URL override found. Generating a new pre-signed URL.")

        s3_path = f"{get_settings().UPLOADED_MODEL_FILES_BUCKET}/{model_id}/{body.fileName}"

        s3 = S3Client()
        try:
            pre_signed_url = s3.get_put_presigned_url(s3_path)
            logger.info(f"Generated pre-signed URL: {pre_signed_url}")
            _record_pending_upload(db, model_id, body.fileName, body.fileSize)
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
