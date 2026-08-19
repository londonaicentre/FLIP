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

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, validator

from flip_api.domain.schemas.status import ModelStatus, ProjectStatus
from flip_api.domain.schemas.users import CognitoUser

# Blocks the three characters most likely to enable structural XML injection
# downstream in the imaging-api projectData payload. This is not exhaustive —
# ElementTree also escapes " and ' where they're significant (attribute values),
# but the project name lands as element text where they aren't, so blocking
# them at this layer is unnecessary. The downstream serializer is the canonical
# defence; this validator is the hub-boundary fail-fast that returns a 422
# instead of letting the corrupted name flow to the trust.
_XML_FORBIDDEN_CHARS = ("<", ">", "&")


def _reject_xml_control_chars(v: str) -> str:
    if any(c in v for c in _XML_FORBIDDEN_CHARS):
        raise ValueError(f"name must not contain XML control characters {_XML_FORBIDDEN_CHARS}")
    return v


class IProjectQuery(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    name: str = Field()
    query: str = Field()
    # Trust IDs the query was dispatched to at submit time. Drives the
    # per-trust panel's visibility set: a trust that errored or never
    # responded (offline, hub rejected its error report) stays visible —
    # as an error chip or a "running" pulse — instead of vanishing.
    # ``len(queried_trust_ids)`` is the "trusts queried" count.
    queried_trust_ids: list[UUID] = Field(default_factory=list, alias="queriedTrustIds")
    # Subset of ``queried_trust_ids`` whose ``TrustTask`` is still PENDING
    # — the trust hasn't polled the hub for it yet. UI renders these with
    # a "queued" chip instead of "running" so the operator can tell the
    # difference between "we sent it, awaiting pickup" and "trust is
    # actively processing".
    pending_trust_ids: list[UUID] = Field(default_factory=list, alias="pendingTrustIds")
    # Subset of ``queried_trust_ids`` whose ``TrustTask`` was cancelled
    # (typically because the project was approved without them). UI shows
    # these as a greyed-out "skipped" chip — they were dispatched but the
    # work was abandoned before they could pick it up.
    cancelled_trust_ids: list[UUID] = Field(default_factory=list, alias="cancelledTrustIds")
    # Subset of ``queried_trust_ids`` that posted any QueryResult row
    # (successful or errored). The staging guard requires this — a trust
    # that was dispatched but never replied has no cohort count and so
    # must not be stage-eligible. Combined with ``errored_trust_ids``,
    # the stageable set is ``responded - errored``.
    responded_trust_ids: list[UUID] = Field(default_factory=list, alias="respondedTrustIds")
    # Subset of ``responded_trust_ids`` whose data blob carried a
    # non-null ``error``. Excluded from staging — even though we got a
    # reply, we have no usable cohort count.
    errored_trust_ids: list[UUID] = Field(default_factory=list, alias="erroredTrustIds")
    # Subset of ``responded_trust_ids`` (disjoint from ``errored_trust_ids``) whose
    # cohort count is 0 — either a genuine zero match or a privacy-suppressed
    # below-threshold count (see issue #519). Excluded from staging: there is no
    # cohort to build an imaging project against.
    empty_trust_ids: list[UUID] = Field(default_factory=list, alias="emptyTrustIds")
    total_cohort: int | None = Field(default=None, alias="totalCohort")
    created: str | None = Field(default=None)
    created_by: str | None = Field(default=None, alias="createdBy")

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


class IApprovedTrust(BaseModel):
    id: UUID
    name: str
    code: str | None = None
    approved: bool
    approved_at: str | None = Field(default=None, alias="approvedAt")

    model_config = ConfigDict(
        populate_by_name=True,
    )


# Base Pydantic Models (Interfaces)
class IProject(BaseModel):  # Base for IProject to avoid repetition
    id: UUID = Field(default_factory=uuid4)
    name: str = Field(default="")
    description: str = Field(default="")
    owner_id: UUID = Field(..., alias="ownerId")
    # Display name of the owner — populated from UserProfile by the list
    # endpoint so cards/rows can show "Owned by …" without a follow-up
    # round-trip per project. Null only if the owner has no UserProfile row
    # (very old seeded users). The detail endpoint returns `IProjectResponse`
    # instead of this base class and does not surface this field.
    owner_name: str | None = Field(default=None, alias="ownerName")
    # Total users with project access — includes the owner, who's auto-added
    # to ProjectUserAccess on project creation (so the UI doesn't need to +1).
    user_count: int = Field(default=0, alias="userCount")
    deleted: bool = Field(default=False)
    approved: bool | None = None
    creation_timestamp: str = Field(..., alias="creationtimestamp")  # This is a string to match the Vue.js handling
    # Most-recent STAGE audit row's timestamp. Null if the project was never
    # staged (i.e. it's still in UNSTAGED). String for the same Vue.js
    # handling reason as creation_timestamp.
    staged_at: str | None = Field(default=None, alias="stagedAt")
    status: ProjectStatus = Field(default=ProjectStatus.UNSTAGED)
    # Participating trusts + most-recent cohort query. Optional so the list
    # endpoint can omit them when batch-loading isn't worth it; the detail
    # endpoint always populates both. Lives on IProject (not just IReturnedProject)
    # so the projects-list UI can render trust chips + cohort sizes without a
    # follow-up round-trip per row.
    approved_trusts: list[IApprovedTrust] | None = Field(default=None, alias="approvedTrusts")
    query: IProjectQuery | None = Field(default=None)

    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,  # Still valid for things like datetime, UUID, etc.
    )


class IReturnedProject(IProject):  # Extends IProject
    owner_email: EmailStr = Field(..., alias="ownerEmail")
    # Narrow by construction, not by declaration. `CognitoUser` permits `name` and `organisation`,
    # but they stay empty here because `get_project` fills this list from `get_user_by_email_or_id`
    # (which populates id/email/is_disabled alone) and never calls `apply_user_profile` — the DB
    # join that is the only source of those two fields. Do not enrich project members with display
    # names: that would widen full-profile disclosure to every project co-member, which is the leak
    # FLIP#907 closed on `/users/{user_id}`. The shape to hold to is `ProjectMemberLookup`; typing
    # this field as one so the response model enforces it rather than a field default is FLIP#996,
    # which is not a one-line change — `get_project` passes `CognitoUser` instances, and that field
    # type rejects them at construction.
    users: list[CognitoUser]
    # Surfaced read-only so the edit form can display the project's
    # DICOM->NIfTI setting. Fixed at creation and immutable afterwards (the
    # edit endpoint ignores it), so the UI renders the toggle disabled.
    dicom_to_nifti: bool = Field(default=True)
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
    def replace_null_list(cls, value: list[UUID] | None) -> list[UUID]:
        if value is None:
            return []
        if isinstance(value, list) and all(v is None for v in value):
            return []
        return value

    _validate_name_xml = field_validator("name")(_reject_xml_control_chars)


class IProjectDetails(BaseModel):
    name: str = Field(..., description="Project name")
    description: str = Field(default="", description="Project description")
    users: list[UUID] | None = Field(default=[], description="List of user IDs to add to the project")  # type: ignore[arg-type]

    _validate_name_xml = field_validator("name")(_reject_xml_control_chars)


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
