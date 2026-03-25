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

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from flip_api.domain.schemas.status import BucketAction, BucketStatus, FileUploadStatus
from flip_api.domain.schemas.types import NonEmptyUUIDList


class ModelFiles(BaseModel):
    """Model for file paths in the model."""

    algo: str | None = None
    opener: str | None = None
    model: str | None = None


class ModelFilesList(BaseModel):
    """Model for list of model files."""

    files: ModelFiles


class ScannedFileSns(BaseModel):
    """Model for SNS message when a file is scanned."""

    message: str


class ScannedFileMessage(BaseModel):
    """Model for the message received when a file is scanned."""

    bucket: str
    key: str
    status: BucketStatus
    action: BucketAction
    finding: str


class ScannedFileInput(BaseModel):
    Records: list[dict[str, Any]]


class UploadFileBody(BaseModel):
    """Model for file upload request body."""

    fileName: str = Field(..., description="Name of the file to upload")


class ModelFileDelete(BaseModel):
    model_id: UUID
    fileName: str
    user_id: UUID


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

    fileName: str
    model_id: UUID
    user_id: UUID


class IdList(BaseModel):
    """
    Model for a list of UUIDs.

    Enforces that it's a list with at least 1 element.
    Ensures each item in the list is a valid UUID.
    """

    ids: NonEmptyUUIDList
