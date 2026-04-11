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
from typing import Annotated
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, EmailStr, Field, validator

from flip_api.domain.schemas.status import ModelStatus, ProjectStatus


class IProjectQuery(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    name: str = Field()
    query: str = Field()
    trusts_queried: int | None = Field(default=None, alias="trustsQueried")
    total_cohort: int | None = Field(default=None, alias="totalCohort")

    model_config = ConfigDict(
        populate_by_name=True,
    )


class IProjectResponse(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    name: str
    description: str = Field(default="")
    query: IProjectQuery | None = None
    owner_id: UUID = Field(..., alias="ownerId")
    creation_timestamp: Annotated[datetime, Field(default_factory=datetime.utcnow)]
    status: ProjectStatus = Field(default=ProjectStatus.UNSTAGED)
    query_id: UUID | None = Field(default=None)
    dicom_to_nifti: bool = Field(default=True)

    model_config = ConfigDict(
        populate_by_name=True,
    )


# Base Pydantic Models (Interfaces)
class IProject(BaseModel):  # Base for IProject to avoid repetition
    id: UUID = Field(default_factory=uuid4)
    name: str = Field(default="")
    description: str = Field(default="")
    owner_id: UUID = Field(..., alias="ownerId")
    deleted: bool = Field(default=False)
    approved: bool | None = None
    creation_timestamp: str = Field(..., alias="creationtimestamp")  # This is a string to match the Vue.js handling
    status: ProjectStatus = Field(default=ProjectStatus.UNSTAGED)

    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,  # Still valid for things like datetime, UUID, etc.
    )


class IApprovedTrust(BaseModel):
    id: UUID
    name: str
    approved: bool


class IReturnedProject(IProject):  # Extends IProject
    owner_email: EmailStr = Field(..., alias="ownerEmail")
    approved_trusts: list[IApprovedTrust] | None = Field(default=None, alias="approvedTrusts")
    query: IProjectQuery | None = Field(default=None)
    users: list[EmailStr]
    model_config = ConfigDict(populate_by_name=True)


class IModelsInfoResponse(BaseModel):
    id: UUID
    name: str
    description: str
    status: ModelStatus
    owner_id: UUID = Field()  # Alias for potential exact match

    model_config = ConfigDict(
        populate_by_name=True,
    )


# These might already be defined in flip.domain.schemas.projects
# If so, they should be imported from there to avoid duplication.
# For this exercise, I'm redefining them based on the provided interfaces.ts.
# Ensure these definitions are consistent with any existing ones or consolidate them.


class IEditProject(BaseModel):
    name: str = Field(..., description="Project name")
    description: str = Field(default="", description="Project description")
    users: list[UUID] | None = Field(default=[], description="List of user IDs to add to the project")  # type: ignore[arg-type]

    # Handles cases where no users are added when editing a project (in which case the input is '[null]')
    @validator("users", pre=True)
    def replace_null_list(cls, value):
        if value is None:
            return []
        if isinstance(value, list) and all(v is None for v in value):
            return []
        return value


class IProjectDetails(BaseModel):
    name: str = Field(..., description="Project name")
    description: str = Field(default="", description="Project description")
    users: list[UUID] | None = Field(default=[], description="List of user IDs to add to the project")  # type: ignore[arg-type]


class IProjectApproval(BaseModel):
    project_id: UUID = Field(..., description="The ID of the project to approve.")
    trust_ids: list[UUID] = Field(
        ...,
        description="List of Trust IDs to approve the project for.",
    )


class ICountResponse(BaseModel):
    count: int


class IStageProjectRequest(BaseModel):
    trusts: list[UUID]


# Imaging Related Interfaces
class IImagingImportStatus(BaseModel):
    successful_count: int = Field(alias="successful")
    failed_count: int = Field(alias="failed")
    processing_count: int = Field(alias="processing")
    queued_count: int = Field(alias="queued")
    queue_failed_count: int = Field(alias="queueFailed")

    model_config = ConfigDict(
        populate_by_name=True,
    )


class IImagingStatusResponse(BaseModel):
    project_creation_completed: bool = Field(alias="projectCreationCompleted")
    import_status: IImagingImportStatus | None = Field(default=None, alias="importStatus")
    reimport_count: int | None = Field(default=None, alias="reimportCount")

    model_config = ConfigDict(
        populate_by_name=True,
    )


class IImagingStatus(IImagingStatusResponse):  # Extends IImagingStatusResponse
    trust_id: UUID = Field(alias="trustId")
    trust_name: str = Field(alias="trustName")

    model_config = ConfigDict(
        populate_by_name=True,
    )


class IUpdateXnatProfile(BaseModel):
    email: EmailStr
    enabled: bool


class IImagingProjectStatusParams(BaseModel):
    project_id: UUID = Field()
    query_id: UUID = Field()

    model_config = ConfigDict(
        populate_by_name=True,
    )


class IReimportQuery(BaseModel):
    query_id: UUID = Field()
    query: str = Field()
    xnat_project_id: UUID = Field()
    last_reimport: Annotated[datetime | None, Field(default_factory=datetime.utcnow)]
    trust_id: UUID = Field()
    trust_name: str = Field()

    model_config = ConfigDict(
        populate_by_name=True,
    )


class IReimportResponse(BaseModel):
    xnat_project_id: UUID = Field()
    trust_id: UUID = Field()
    trust_name: str = Field()
    status: int  # HTTP status code or similar

    model_config = ConfigDict(
        populate_by_name=True,
    )
