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

"""A job's FL server uploads its results through URLs the hub presigns for that job's own object.

The FL server runs researcher-supplied job code, so it holds no object-store credentials and no bucket
name. It asks for a multipart upload of ``<model_id>/<file_name>``, then for one URL per part, then for the
upload to be completed, each call under the job's own credential (``authorise_job_callback``).

What the caller gets back for a started upload is a sealed handle, not the object store's upload id: the
file name, the upload id and the declared size, encrypted under the hub's key and bound to the model. Every
later call presents it, so the hub signs only for an upload it started for that model, and only for the
parts the declared size has.
"""

import math
from uuid import UUID

from botocore.exceptions import BotoCoreError, ClientError
from cryptography.exceptions import InvalidTag
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from flip_api.auth.job_callback import authorise_job_callback
from flip_api.config import get_settings
from flip_api.domain.schemas.private import (
    RESULTS_UPLOAD_PART_BYTES,
    ResultsUpload,
    ResultsUploadAbort,
    ResultsUploadCompletion,
    ResultsUploadPart,
    ResultsUploadPartRequest,
    ResultsUploadRequest,
)
from flip_api.utils.encryption import decrypt, encrypt
from flip_api.utils.logger import logger
from flip_api.utils.s3_client import PresignAudience, S3Client

router = APIRouter(tags=["private_services"])

# One part is uploaded per URL, straight after asking for it. Short also because a URL signed with the
# task role's credentials dies with them, which can be minutes away.
PART_URL_TTL_SECONDS = 600

S3_ERRORS = (BotoCoreError, ClientError)


class _Upload(BaseModel):
    """A started upload, as sealed into its handle."""

    file_name: str
    upload_id: str
    size: int

    @property
    def part_count(self) -> int:
        return math.ceil(self.size / RESULTS_UPLOAD_PART_BYTES)


def _context(model_id: UUID) -> str:
    """Encryption context of a handle: one model's results upload, so it opens for no other model or purpose."""
    return f"results_upload:{model_id}"


def _open(model_id: UUID, handle: str) -> _Upload:
    """The upload a handle was sealed for, refusing anything the hub did not issue for this model."""
    try:
        return _Upload.model_validate_json(decrypt(handle, context=_context(model_id)))
    except (InvalidTag, KeyError, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown upload.")


def _results_prefix(model_id: UUID) -> str:
    """The model's own prefix, the one the results download lists."""
    return f"{get_settings().UPLOADED_FEDERATED_DATA_BUCKET}/{model_id}/"


def _refuse(step: str, model_id: UUID, error: Exception) -> HTTPException:
    """Log an object-store failure by type only (its message may carry a signed URL) and build the 500."""
    logger.error(f"Results upload for model {model_id} failed to {step} error_type={type(error).__name__}")
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Unable to {step} the results upload."
    )


@router.post("/model/{model_id}/results-upload", response_model=ResultsUpload)
def start_results_upload(
    model_id: UUID,
    request: ResultsUploadRequest,
    _: None = Depends(authorise_job_callback),
) -> ResultsUpload:
    """
    Start the upload of a model's results file, aborting any upload of that model still in progress.

    Internal-only, and only for a model that has a job still reporting (``authorise_job_callback``).

    Args:
        model_id (UUID): The ID of the model.
        request (ResultsUploadRequest): The name and size of the zip file.

    Returns:
        ResultsUpload: The upload's handle and the size of every part but the last.

    Raises:
        HTTPException: 413 when the file is larger than ``MAX_RESULTS_FILE_BYTES``; 500 when the object
            store refuses the upload.
    """
    if request.size > get_settings().MAX_RESULTS_FILE_BYTES:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="Results file too large.")

    prefix = _results_prefix(model_id)
    s3 = S3Client()
    try:
        # One upload per model at a time: an earlier one that never completed is discarded, parts and all.
        aborted = s3.abort_multipart_uploads(prefix)
        upload_id = s3.create_multipart_upload(prefix + request.file_name)
    except S3_ERRORS as e:
        raise _refuse("start", model_id, e)
    logger.info(
        f"Started results upload for model {model_id}: {request.file_name}, {request.size} bytes"
        f" ({aborted} unfinished upload(s) aborted)"
    )
    upload = _Upload(file_name=request.file_name, upload_id=upload_id, size=request.size)
    return ResultsUpload(
        upload=encrypt(upload.model_dump_json(), context=_context(model_id)), part_size=RESULTS_UPLOAD_PART_BYTES
    )


@router.post("/model/{model_id}/results-upload/part", response_model=ResultsUploadPart)
def presign_results_upload_part(
    model_id: UUID,
    part: ResultsUploadPartRequest,
    _: None = Depends(authorise_job_callback),
) -> ResultsUploadPart:
    """
    Presign the URL one part of a model's results file is PUT to.

    Internal-only, and only for a model that has a job still reporting (``authorise_job_callback``).

    Args:
        model_id (UUID): The ID of the model.
        part (ResultsUploadPartRequest): The upload's handle and the part number.

    Returns:
        ResultsUploadPart: The URL, valid for ``PART_URL_TTL_SECONDS`` and for a body of that part's size.

    Raises:
        HTTPException: 400 for a handle the hub did not issue for this model, or a part the declared size
            does not have; 500 when the URL cannot be signed.
    """
    upload = _open(model_id, part.upload)
    if part.part_number > upload.part_count:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No such part.")
    try:
        # The FL server opens the URL, over the same route as the app bundles it is handed.
        url = S3Client().get_upload_part_presigned_url(
            _results_prefix(model_id) + upload.file_name,
            upload.upload_id,
            part.part_number,
            # Every part is full but the last, which takes the rest.
            min(RESULTS_UPLOAD_PART_BYTES, upload.size - (part.part_number - 1) * RESULTS_UPLOAD_PART_BYTES),
            audience=PresignAudience.INTERNAL,
            expiration=PART_URL_TTL_SECONDS,
        )
    except S3_ERRORS as e:
        raise _refuse("presign", model_id, e)
    return ResultsUploadPart(url=url)


@router.post("/model/{model_id}/results-upload/complete", response_model=dict[str, str])
def complete_results_upload(
    model_id: UUID,
    completion: ResultsUploadCompletion,
    _: None = Depends(authorise_job_callback),
) -> dict[str, str]:
    """
    Assemble the uploaded parts into the model's results file. An upload that cannot be completed is aborted.

    Internal-only, and only for a model that has a job still reporting (``authorise_job_callback``).

    Args:
        model_id (UUID): The ID of the model.
        completion (ResultsUploadCompletion): The upload's handle and its parts' ETags.

    Returns:
        dict[str, str]: A confirmation message.

    Raises:
        HTTPException: 400 for a handle the hub did not issue for this model, or a number of parts other
            than the declared size has; 500 when the object store refuses to complete the upload.
    """
    upload = _open(model_id, completion.upload)
    if len(completion.etags) != upload.part_count:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Wrong number of parts.")
    path = _results_prefix(model_id) + upload.file_name
    s3 = S3Client()
    try:
        s3.complete_multipart_upload(path, upload.upload_id, completion.etags)
    except S3_ERRORS as e:
        try:
            s3.abort_multipart_upload(path, upload.upload_id)
        except S3_ERRORS as abort_error:
            logger.error(f"Results upload for model {model_id} not aborted error_type={type(abort_error).__name__}")
        raise _refuse("complete", model_id, e)
    logger.info(f"Completed results upload for model {model_id}: {upload.file_name}")
    return {"detail": "Created"}


@router.post("/model/{model_id}/results-upload/abort", response_model=dict[str, str])
def abort_results_upload(
    model_id: UUID,
    abort: ResultsUploadAbort,
    _: None = Depends(authorise_job_callback),
) -> dict[str, str]:
    """
    Abort a model's results upload, discarding the parts uploaded so far.

    Internal-only, and only for a model that has a job still reporting (``authorise_job_callback``).

    Args:
        model_id (UUID): The ID of the model.
        abort (ResultsUploadAbort): The upload's handle.

    Returns:
        dict[str, str]: A confirmation message.

    Raises:
        HTTPException: 400 for a handle the hub did not issue for this model; 500 when the object store
            refuses to abort the upload.
    """
    upload = _open(model_id, abort.upload)
    try:
        S3Client().abort_multipart_upload(_results_prefix(model_id) + upload.file_name, upload.upload_id)
    except S3_ERRORS as e:
        raise _refuse("abort", model_id, e)
    logger.info(f"Aborted results upload for model {model_id}: {upload.file_name}")
    return {"detail": "Aborted"}
