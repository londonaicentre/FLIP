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
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, validator

from flip_api.db.models.main_models import UploadedFiles
from flip_api.domain.schemas.actions import ModelAuditAction
from flip_api.domain.schemas.status import ModelStatus, TrustIntersectStatus


class ModelStatusEdit(str, Enum):
    PENDING = "PENDING"


class ImageType(str, Enum):
    CLIENT = "CLIENT"
    SERVER = "SERVER"


class IImage(BaseModel):
    imageRef: str


class IModelDetails(BaseModel):
    name: str = Field(..., max_length=75)
    description: str = Field("", max_length=250)


class ISaveModel(IModelDetails):
    project_id: UUID = Field(..., alias="projectId")


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

    model_config = ConfigDict(
        populate_by_name=True,
    )


class IBuildImagesForModel(BaseModel):
    files: dict = Field(...)

    @validator("files")
    def validate_files(cls, v):
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


class IModelMetricsValue(BaseModel):
    xValue: int
    yValue: float


class IModelMetricsData(BaseModel):
    data: list[IModelMetricsValue]
    seriesLabel: str


class IModelMetrics(BaseModel):
    yLabel: str
    xLabel: str
    metrics: list[IModelMetricsData]
