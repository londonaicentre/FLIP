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
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, validator

from flip_api.db.models.main_models import UploadedFiles
from flip_api.domain.schemas.actions import ModelAuditAction
from flip_api.domain.schemas.status import ModelStatus, TrustIntersectStatus


class ModelStatusEdit(StrEnum):
    PENDING = "PENDING"


class IModelDetails(BaseModel):
    name: str = Field(..., max_length=75)
    description: str = Field("", max_length=250)


class ISaveModel(IModelDetails):
    project_id: UUID = Field(..., alias="projectId")


class ITrustSummary(BaseModel):
    """A trust participating in a model's federated run, for the Models-list chips."""

    id: UUID
    name: str
    code: str | None = Field(default=None)

    model_config = ConfigDict(populate_by_name=True)


class IAllModelsResponse(BaseModel):
    """One row of the estate-wide Models list (issue #726).

    Joins a model to its owning project (name), the owner's display name and the
    model's run trusts. ``owner_name`` is null when the owner has no UserProfile
    row; ``trusts`` is the latest FL job's dispatch roster (fl_job_trust), so it
    is empty until training is initiated.
    """

    id: UUID
    name: str
    description: str = ""
    status: ModelStatus
    project_id: UUID = Field(..., alias="projectId")
    project_name: str = Field(..., alias="projectName")
    owner_id: UUID = Field(..., alias="ownerId")
    owner_name: str | None = Field(default=None, alias="ownerName")
    trusts: list[ITrustSummary] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)


class IModelsListResponse(BaseModel):
    """Paginated Models-list page plus per-status totals for the filter tiles (issue #726).

    ``status_counts`` maps each ``ModelStatus`` value to its count across the caller's
    access-scoped set, honouring search but **ignoring** the active status filter — so the
    tiles keep their numbers while one is selected.
    """

    page: int
    page_size: int = Field(alias="pageSize")
    total_pages: int = Field(alias="totalPages")
    total_records: int = Field(alias="totalRecords")
    data: list[IAllModelsResponse]
    status_counts: dict[str, int] = Field(default_factory=dict, alias="statusCounts")

    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)


class IModelLog(BaseModel):
    timestamp: datetime = Field(alias="@timestamp")
    model: str
    status: str
    trust: str | None = None
    message: str


class ISourceLog(BaseModel):
    _source: IModelLog
    _id: str


class IModelStatus(BaseModel):
    modelStatus: str


class ModelTrustIntersectStatus(BaseModel):
    status: TrustIntersectStatus


# ---------------------------
# Deeply Nested Stats Models
# ---------------------------


class Age(BaseModel):
    Mean: float


class Gender(BaseModel):
    Male: int
    Female: int
    MissingData: int


class ClientVisit(BaseModel):
    Emergency: int
    Inpatient: int
    MissingData: int


class Statistics(BaseModel):
    TotalCount: int
    Age: Age
    Gender: Gender
    ClientVisit: ClientVisit


class TrustsResults(BaseModel):
    Data: Statistics
    TrustName: str


# ---------------------------


class IQuery(BaseModel):
    id: UUID
    name: str
    query: str
    results: list[TrustsResults] | None = Field(default=None)


class IModelResponse(BaseModel):
    model_id: UUID = Field(..., alias="modelId")
    model_name: str = Field(..., alias="modelName")
    model_description: str = Field(..., alias="modelDescription")
    project_id: UUID = Field(..., alias="projectId")
    status: ModelStatus
    query: IQuery | None = Field(default=None)
    files: list[UploadedFiles] | None = None
    # Lifecycle timestamps surfaced so the model lifecycle bar can render real
    # dates instead of static "done" labels. Each is the latest models_audit
    # row for the corresponding action; None until the model has reached that
    # stage. ``creation_timestamp`` is just ``Model.creation_timestamp``.
    creation_timestamp: str | None = Field(default=None, alias="creationTimestamp")
    prepared_at: str | None = Field(default=None, alias="preparedAt")
    training_started_at: str | None = Field(default=None, alias="trainingStartedAt")
    results_uploaded_at: str | None = Field(default=None, alias="resultsUploadedAt")
    # The trusts the run was dispatched to — the latest FL job's fl_job_trust
    # roster. Empty before dispatch, when the model has no job yet.
    trusts: list[ITrustSummary] = Field(default_factory=list)

    model_config = ConfigDict(
        populate_by_name=True,
    )


class IBuildImagesForModel(BaseModel):
    files: dict = Field(...)

    @validator("files")
    def validate_files(cls, v: dict) -> dict:
        required_keys = {"opener", "algo", "model"}
        if not isinstance(v, dict):
            raise ValueError("'files' must be an object")
        missing = required_keys - v.keys()
        if missing:
            raise ValueError(f"'files' must contain keys: {', '.join(missing)}")
        for key in required_keys:
            if not isinstance(v.get(key), str) or not v[key].strip():
                raise ValueError(f"'{key}' must be a non-empty string")
        return v


class IDetailedModelStatus(BaseModel):
    status: ModelStatus
    deleted: bool


class ILog(BaseModel):
    id: UUID
    model_id: UUID = Field(..., alias="modelId")
    log_date: datetime = Field(..., alias="logDate")
    success: bool
    trust_name: str | None = Field(default=None, alias="trustName")
    # Trust short code (GSTT/KCH) for compact display; null for hub rows and
    # trusts without a code. The round the row belongs to (1-based) is exposed
    # for round-aware grouping in the UI; null on legacy/free-text rows.
    trust_code: str | None = Field(default=None, alias="trustCode")
    global_round: int | None = Field(default=None, alias="globalRound")
    # Always populated on serve: free text verbatim, or event rows rendered by
    # log_rendering.render_log.
    log: str

    model_config = ConfigDict(
        populate_by_name=True,
    )


class IModelAuditAction(BaseModel):
    model_id: UUID
    action: ModelAuditAction
    userid: str


class ITrainingMetricsResponse(BaseModel):
    trust: str
    globalround: int
    label: str
    result: float


# Response-only models: Python attributes are snake_case; the wire format the UI
# reads stays camelCase via serialization aliases (FastAPI serialises
# response_model by alias).
class IModelMetricsValue(BaseModel):
    x_value: int = Field(..., serialization_alias="xValue")
    y_value: float = Field(..., serialization_alias="yValue")


class IModelMetricsData(BaseModel):
    data: list[IModelMetricsValue]
    series_label: str = Field(..., serialization_alias="seriesLabel")


class IModelMetrics(BaseModel):
    y_label: str = Field(..., serialization_alias="yLabel")
    x_label: str = Field(..., serialization_alias="xLabel")
    metrics: list[IModelMetricsData]
