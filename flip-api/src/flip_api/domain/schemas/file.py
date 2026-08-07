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

from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator, BaseModel, Field, field_validator

from flip_api.domain.schemas.status import FileUploadStatus
from flip_api.domain.schemas.types import NonEmptyUUIDList


def validate_safe_file_name(value: str) -> str:
    """Reject any character that would let the caller steer an S3 key off-prefix.

    Used at every boundary where caller-supplied text is concatenated into an
    S3 object key — request bodies (``UploadFileBody``) and FastAPI path
    parameters on the download/delete routes. Without this guard a value like
    ``../other-model/x`` or one containing NUL/control bytes would write
    outside the ``{model_id}/`` prefix or smuggle an unexpected key past the
    downstream scan/download endpoints.

    Args:
        value (str): Caller-supplied filename to validate.

    Returns:
        str: The value unchanged when every rule passes.

    Raises:
        ValueError: If the value contains whitespace at the edges, path
            separators, a path-traversal token, or control characters.
    """
    if not value:
        raise ValueError("file name must not be empty")
    if value != value.strip():
        raise ValueError("file name must not have leading or trailing whitespace")
    if "/" in value or "\\" in value:
        raise ValueError("file name must not contain path separators")
    if value in (".", "..") or value.startswith(".."):
        raise ValueError("file name must not be a path-traversal token")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in value):
        raise ValueError("file name must not contain control characters")
    return value


SafeFileName = Annotated[str, AfterValidator(validate_safe_file_name)]


class ModelFiles(BaseModel):
    """Model for file paths in the model."""

    algo: str | None = None
    opener: str | None = None
    model: str | None = None


class ModelFilesList(BaseModel):
    """Model for list of model files."""

    files: ModelFiles


class UploadFileBody(BaseModel):
    """Model for file upload request body.

    Field names are camelCase rather than snake_case because they map
    directly to the case-sensitive form-field names in the multipart
    upload (e.g. S3's ``Content-Type`` form field), and to stay
    consistent with the existing ``fileName`` boundary.
    """

    fileName: str = Field(..., description="Name of the file to upload", min_length=1, max_length=255)
    contentType: str | None = Field(
        default=None,
        description=(
            "Content-Type the client intends to upload with. When provided, "
            "the presigned POST policy locks the upload's Content-Type to "
            "this exact value."
        ),
    )

    @field_validator("fileName")
    @classmethod
    def _validate_file_name(cls, v: str) -> str:
        return validate_safe_file_name(v)


class PresignedUploadResponse(BaseModel):
    """Response for a presigned-POST upload request.

    The client must POST ``multipart/form-data`` to ``url`` with every entry
    in ``fields`` included as a form field, plus the file last under the
    field name ``file``. ``maxBytes`` mirrors the size cap baked into the
    policy so the UI can short-circuit oversized files locally and surface
    a clear error.
    """

    url: str
    fields: dict[str, str]
    maxBytes: int


class PresignedDownloadResponse(BaseModel):
    """Response for a presigned-GET model-file download request.

    The client fetches the file's bytes directly from ``url`` (a short-lived
    presigned S3 GET), bypassing flip-api entirely for the transfer itself.
    """

    url: str
    fileName: str


class ModelFile(BaseModel):
    id: str | None = None
    name: str
    size: int | None = None
    type: str | None = None
    status: FileUploadStatus | None = None
    model_id: UUID
    created: str | None = None
    modified: str | None = None


class PreSignedUrlResponse(BaseModel):
    """Response model for pre-signed URL requests."""

    fileName: str
    presignedUrl: str
    status: FileUploadStatus
    model_id: str


class ModelFileDownload(BaseModel):
    """Model for file download requests."""

    fileName: SafeFileName
    model_id: UUID
    user_id: UUID


class IdList(BaseModel):
    """
    Model for a list of UUIDs.

    Enforces that it's a list with at least 1 element.
    Ensures each item in the list is a valid UUID.
    """

    ids: NonEmptyUUIDList
