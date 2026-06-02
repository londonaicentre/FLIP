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

from pydantic import BaseModel, Field, validator

from flip_api.config import get_settings
from flip_api.domain.schemas.status import TaskType


class Results(BaseModel):
    value: str
    count: int


class OmopData(BaseModel):
    name: str
    results: list[Results]


class OmopCohortResults(BaseModel):
    query_id: UUID
    trust_id: UUID
    created: str
    record_count: int
    data: list[OmopData]
    # Populated by trust-api when the cohort query fails (data-access-api error,
    # decryption failure, etc.). When set, the hub records the trust as errored
    # instead of leaving its per-trust UI status stuck on "running".
    error: str | None = None
    # True when the trust privacy-suppressed a non-zero count below COHORT_QUERY_THRESHOLD,
    # rather than genuinely matching zero patients. Set by data-access-api and
    # forwarded verbatim by trust-api. Lets the UI show a "suppressed" chip instead
    # of a literal 0 that reads as "no data available". See issue #519.
    suppressed: bool = False

    @validator("data", pre=True, always=True)
    def ensure_data_is_list(cls, value: Any) -> list[Any]:
        if value is None:
            return []
        return value


class TrainingMetrics(BaseModel):
    fl_client_name: str
    global_round: int = Field(ge=0)
    label: str
    result: float


class TrainingLog(BaseModel):
    fl_client_name: str
    log: str


class ProjectApprovalBody(BaseModel):
    trusts: list[UUID] = Field(..., description="List of Trust IDs to be approved for the project.")


class ProjectApproval(BaseModel):
    project_id: UUID = Field(..., description="Project ID to be approved.")
    trust_ids: list[UUID] = Field(..., description="List of Trust IDs to be approved for the project.")


class TrustSpecificData(BaseModel):  # Parsed from query_result.data JSON string
    record_count: int
    data: list[OmopData]
    error: str | None = None
    # Mirrors OmopCohortResults.suppressed; persisted in QueryResult.data so the
    # aggregator can flag privacy-suppressed trusts. See issue #519.
    suppressed: bool = False


class AggregatedTrustFieldResult(BaseModel):
    data: Any
    trust_name: str
    trust_id: str


class AggregatedFieldResult(BaseModel):
    name: str  # Field name
    results: list[AggregatedTrustFieldResult]


class AggregatedCohortStats(BaseModel):  # Stored as JSON in query_stats.stats
    record_count: int
    trusts_results: list[AggregatedFieldResult]
    # trust_id (str) -> record_count for trusts that responded successfully.
    # Distinguishes "responded with 0 (privacy-suppressed)" from "never
    # responded" so the UI shows 0 instead of staying stuck on "running".
    trust_record_counts: dict[str, int] = Field(default_factory=dict)
    # trust_id (str) -> error message for trusts whose cohort query failed.
    # Mutually exclusive with trust_record_counts for the same trust.
    trust_errors: dict[str, str] = Field(default_factory=dict)
    # trust_ids (str) that privacy-suppressed their count (below threshold).
    # A suppressed trust still appears in trust_record_counts with count 0 — this
    # list tells the UI to render a "suppressed" chip instead of a literal 0 that
    # would read as "no data available". See issue #519.
    trust_suppressed: list[str] = Field(default_factory=list)


# Helper structure for data fetched from DB for aggregation
class FetchedAggregationData(BaseModel):
    trust_name: list[str]
    trust_id: list[str]
    data: list[str]  # List of JSON strings, each is a TrustSpecificData


class TrustTaskResponse(BaseModel):
    """Response model for a single trust task."""

    id: UUID
    task_type: TaskType
    payload: str
    created_at: datetime


class TaskResultInput(BaseModel):
    """Input model for submitting a task result."""

    success: bool
    result: str | None = Field(default=None, max_length=get_settings().MAX_TASK_RESULT_LENGTH)
