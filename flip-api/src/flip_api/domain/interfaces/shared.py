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

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field
from pydantic.alias_generators import to_camel
from sqlalchemy.orm import declarative_base

from flip_api.domain.schemas.status import (
    AccessRequestStatus,
    BucketAction,
    BucketStatus,
    FileUploadStatus,
    FileUploadTag,
)

Base = declarative_base()

# ---------------------------
# Core Types
# ---------------------------

ProcessEnv = dict[str, str | None]


class IId(BaseModel):
    id: UUID


# ---------------------------
# Data Models
# ---------------------------


class IFileInfo:
    name: str
    status: FileUploadStatus
    size: int
    type: str
    tag: FileUploadTag | None


class IScannedFileRecord:
    Sns: "IScannedFileSns"


class IScannedFileSns:
    Message: str


class IScannedFileMessage:
    bucket: str
    key: str
    status: BucketStatus
    action: BucketAction


class IScannedFileInput:
    Records: list[IScannedFileRecord]


class SQLArray:
    array: list[str]


class ICount:
    count: int


class IContext:
    dbConnection: Any  # Replace with actual Sequelize equivalent


class IAccessRequest(BaseModel):
    """Model for new user access requests."""

    email: EmailStr = Field(..., description="Requester's email address")
    full_name: str = Field(..., description="Requester's full name")
    reason_for_access: str = Field(..., description="Reason for requesting access")

    model_config = {
        "alias_generator": to_camel,
        "populate_by_name": True,
    }


class IAccessRequestRecord(BaseModel):
    """A persisted access request, as returned to administrators for triage."""

    id: UUID
    email: str = Field(..., description="Requester's email address")
    full_name: str = Field(..., description="Requester's full name")
    reason_for_access: str = Field(..., description="Reason for requesting access")
    status: AccessRequestStatus = Field(..., description="Lifecycle state of the request")
    email_notified: bool = Field(..., description="Whether the admin-notification email was dispatched")
    handled_by_user_id: UUID | None = Field(
        default=None, description="Cognito sub of the admin who last enrolled/dismissed the request"
    )
    created_at: datetime = Field(..., description="When the request was submitted")
    updated_at: datetime = Field(..., description="When the request was last modified")

    model_config = {
        "alias_generator": to_camel,
        "populate_by_name": True,
        "from_attributes": True,
    }


class IUpdateAccessRequestStatus(BaseModel):
    """Administrator update to an access request's lifecycle status."""

    status: AccessRequestStatus = Field(..., description="New lifecycle state for the request")

    model_config = {
        "alias_generator": to_camel,
        "populate_by_name": True,
    }
