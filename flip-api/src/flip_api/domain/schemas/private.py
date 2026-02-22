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

from typing import Any, List
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, validator


class Results(BaseModel):
    value: str
    count: int


class OmopData(BaseModel):
    name: str
    results: List[Results]


class OmopCohortResults(BaseModel):
    query_id: UUID
    trust_id: UUID
    created: str
    record_count: int
    data: List[OmopData]

    @validator("data", pre=True, always=True)
    def ensure_data_is_list(cls, value):
        if value is None:
            return []
        return value


class TrainingMetrics(BaseModel):
    trust: str
    global_round: int = Field(
        ...,
        ge=0,
        title="global_round",
        description="'global_round' must be >=0",
        alias="globalRound",
    )
    label: str
    result: float

    model_config = ConfigDict(
        populate_by_name=True,
    )


class TrainingLog(BaseModel):
    trust: str
    log: str


class ProjectApprovalBody(BaseModel):
    trusts: List[UUID] = Field(..., description="List of Trust IDs to be approved for the project.")


class ProjectApproval(BaseModel):
    project_id: UUID = Field(..., description="Project ID to be approved.")
    trust_ids: List[UUID] = Field(..., description="List of Trust IDs to be approved for the project.")


class TrustSpecificData(BaseModel):  # Parsed from query_result.data JSON string
    record_count: int
    data: List[OmopData]


class AggregatedTrustFieldResult(BaseModel):
    data: Any
    trust_name: str
    trust_id: str


class AggregatedFieldResult(BaseModel):
    name: str  # Field name
    results: List[AggregatedTrustFieldResult]


class AggregatedCohortStats(BaseModel):  # Stored as JSON in query_stats.stats
    record_count: int
    trusts_results: List[AggregatedFieldResult]


# Helper structure for data fetched from DB for aggregation
class FetchedAggregationData(BaseModel):
    trust_name: List[str]
    trust_id: List[str]
    data: List[str]  # List of JSON strings, each is a TrustSpecificData
