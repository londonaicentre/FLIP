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
from typing import Generic, List, Optional, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, conlist, field_validator
from sqlmodel import SQLModel

from flip_api.domain.schemas.status import XNATImageStatus


class UserAccessInfo(SQLModel):  # Or BaseModel
    id: UUID
    email: str
    # Add other relevant user fields, e.g., is_active, name


class ProjectQueryInfo(SQLModel):  # Or BaseModel
    id: UUID  # This could be the project's query_id or a separate ID for the query object
    query_string: str  # The actual query content


class ApprovedTrustInfo(SQLModel):  # Or BaseModel
    id: UUID  # Or str, depending on your Trust identifier
    name: str
    status: str  # e.g., "Approved", "Pending"


class ProjectDetailResponse(SQLModel):  # Or BaseModel
    id: UUID
    name: str
    description: Optional[str] = None
    status: str
    owner_id: UUID
    created_at: datetime
    updated_at: datetime
    query_id: Optional[UUID] = None  # Assuming your Project model has this

    owner_email: Optional[str] = None
    query_details: Optional[ProjectQueryInfo] = None
    approved_trusts: List[ApprovedTrustInfo] = []
    users_with_access: List[UserAccessInfo] = []


class ProjectListItemSchema(BaseModel):
    id: UUID
    name: str
    description: Optional[str] = None
    owner_id: UUID = Field(alias="ownerId")
    created_at: datetime = Field(alias="created")
    status: str

    model_config = ConfigDict(
        populate_by_name=True,
        from_attributes=True,  # Useful if data source objects have these attributes
    )


T = TypeVar("T")


class PagedResponse(BaseModel, Generic[T]):
    page_number: int = Field(alias="page")
    page_size: int
    total_pages: int
    total_records: int
    data: List[T]

    model_config = ConfigDict(
        populate_by_name=True,
    )


class ImagingProject(BaseModel):
    id: UUID
    xnat_project_id: UUID
    trust_id: UUID
    retrieve_image_status: XNATImageStatus
    name: str
    endpoint: str
    reimport_count: int

    def model_dump(
        self,
        *,
        mode="python",
        include=None,
        exclude=None,
        context=None,
        by_alias=None,
        exclude_unset=False,
        exclude_defaults=False,
        exclude_none=False,
        round_trip=False,
        warnings=True,
        fallback=None,
        serialize_as_any=False,
    ):
        self.id = str(self.id)
        self.xnat_project_id = str(self.xnat_project_id)
        self.trust_id = str(self.trust_id)
        self.retrieve_image_status = self.retrieve_image_status.value
        return super().model_dump(
            mode=mode,
            include=include,
            exclude=exclude,
            context=context,
            by_alias=by_alias,
            exclude_unset=exclude_unset,
            exclude_defaults=exclude_defaults,
            exclude_none=exclude_none,
            round_trip=round_trip,
            warnings=warnings,
            fallback=fallback,
            serialize_as_any=serialize_as_any,
        )


class XnatProjectStatusInfo(BaseModel):
    retrieve_image_status: XNATImageStatus
    reimport_count: int


class ApproveProjectBodyPayload(BaseModel):
    trusts: List[UUID] = Field(..., description="List of Trust IDs to approve for the project.")


class ProjectDetails(BaseModel, from_attributes=True):
    name: str = Field()
    description: Optional[str] = Field(max_length=250, default=None)
    users: List[UUID] = Field(default_factory=list)

    @field_validator("description")
    @classmethod
    def strip_whitespace(cls, value: str):
        if not isinstance(value, str):
            return ""
        return value.strip()

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Cardiovascular Research Initiative",
                "description": "A project to study cardiovascular diseases using federated learning.",
                "users": ["05137965-8f5a-4752-b07f-d986289eac14", "ddde758d-d51e-4d50-bc3d-c639eb3775f0"],
            }
        }
    )


class StageProjectRequest(BaseModel):
    trusts: conlist(UUID, min_length=1) = Field(..., description="A non-empty list of Trust UUIDs")  # type: ignore[valid-type]

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"trusts": ["a1b2c3d4-e5f6-7890-1234-567890abcdef", "b2c3d4e5-f6a7-8901-2345-67890abcdef0"]}
        }
    )
