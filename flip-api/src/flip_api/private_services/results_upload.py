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
"""

from uuid import UUID

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, HTTPException, status

from flip_api.auth.job_callback import authorise_job_callback
from flip_api.config import get_settings
from flip_api.domain.schemas.private import (
    RESULTS_UPLOAD_PART_BYTES,
    ResultsUpload,
    ResultsUploadCompletion,
    ResultsUploadPart,
    ResultsUploadPartRequest,
    ResultsUploadRequest,
)
from flip_api.utils.logger import logger
from flip_api.utils.s3_client import PresignAudience, S3Client

router = APIRouter(tags=["private_services"])

# One part is uploaded per URL, straight after asking for it. Short also because a URL signed with the
# task role's credentials dies with them, which can be minutes away.
PART_URL_TTL_SECONDS = 600


def _results_path(model_id: UUID, file_name: str) -> str:
    """The object the model's results file lands at, under the prefix the results download lists."""
    return f"{get_settings().UPLOADED_FEDERATED_DATA_BUCKET}/{model_id}/{file_name}"


def _refuse(step: str, model_id: UUID, error: Exception) -> HTTPException:
    """Log an object-store failure by type only (its message may carry a signed URL) and build the 500."""
    logger.error(f"Results upload for model {model_id} failed to {step} error_type={type(error).__name__}")
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Unable to {step} the results upload."
    )


@router.post("/model/{model_id}/results-upload", response_model=ResultsUpload)
def start_results_upload(
    model_id: UUID,
    upload: ResultsUploadRequest,
    _: None = Depends(authorise_job_callback),
) -> ResultsUpload:
    """
    Start the upload of a model's results file.

    Internal-only, and only for a model that has a job still reporting (``authorise_job_callback``).

    Args:
        model_id (UUID): The ID of the model.
        upload (ResultsUploadRequest): The name and size of the zip file.

    Returns:
        ResultsUpload: The upload id and the size of every part but the last.

    Raises:
        HTTPException: 500 when the object store refuses the upload.
    """
    try:
        upload_id = S3Client().create_multipart_upload(_results_path(model_id, upload.file_name))
    except (BotoCoreError, ClientError) as e:
        raise _refuse("start", model_id, e)
    logger.info(f"Started results upload for model {model_id}: {upload.file_name}, {upload.size} bytes")
    return ResultsUpload(upload_id=upload_id, part_size=RESULTS_UPLOAD_PART_BYTES)


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
        part (ResultsUploadPartRequest): The upload and the part number.

    Returns:
        ResultsUploadPart: The URL, valid for ``PART_URL_TTL_SECONDS``.

    Raises:
        HTTPException: 500 when the URL cannot be signed.
    """
    try:
        # The FL server opens the URL, over the same route as the app bundles it is handed.
        url = S3Client().get_upload_part_presigned_url(
            _results_path(model_id, part.file_name),
            part.upload_id,
            part.part_number,
            audience=PresignAudience.INTERNAL,
            expiration=PART_URL_TTL_SECONDS,
        )
    except (BotoCoreError, ClientError) as e:
        raise _refuse("presign", model_id, e)
    return ResultsUploadPart(url=url)


@router.post("/model/{model_id}/results-upload/complete", response_model=dict[str, str])
def complete_results_upload(
    model_id: UUID,
    completion: ResultsUploadCompletion,
    _: None = Depends(authorise_job_callback),
) -> dict[str, str]:
    """
    Assemble the uploaded parts into the model's results file.

    Internal-only, and only for a model that has a job still reporting (``authorise_job_callback``).

    Args:
        model_id (UUID): The ID of the model.
        completion (ResultsUploadCompletion): The upload and its parts' ETags.

    Returns:
        dict[str, str]: A confirmation message.

    Raises:
        HTTPException: 500 when the object store refuses to complete the upload.
    """
    try:
        S3Client().complete_multipart_upload(
            _results_path(model_id, completion.file_name), completion.upload_id, completion.etags
        )
    except (BotoCoreError, ClientError) as e:
        raise _refuse("complete", model_id, e)
    logger.info(f"Completed results upload for model {model_id}: {completion.file_name}")
    return {"detail": "Created"}
