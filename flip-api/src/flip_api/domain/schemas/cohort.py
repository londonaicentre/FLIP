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

from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CohortResultsAge(BaseModel):
    """Model for age statistics in cohort results."""

    mean: float


class CohortResultsGender(BaseModel):
    """Model for gender statistics in cohort results."""

    male: int
    female: int
    MissingData: int


class CohortResultsClientVisit(BaseModel):
    """Model for client visit statistics in cohort results."""

    Inpatient: int
    Emergency: int
    MissingData: int


class ResultsStats(BaseModel):
    """Model for results statistics in cohort results."""

    TotalCount: int
    Age: CohortResultsAge
    Gender: CohortResultsGender
    ClientVisit: CohortResultsClientVisit


class ReceivedCohortResults(BaseModel):
    """Model for received cohort results."""

    project_id: str
    query_id: str
    trust_id: str
    QueryName: str
    Result: ResultsStats


class CohortResultDataResponse(BaseModel):
    """Response model for cohort result data."""

    data: str


class Results(BaseModel):
    """Model for individual results in OMOP trust results."""

    value: str
    count: int


class OMOPTrustResults(BaseModel):
    """Model for OMOP trust results."""

    trust_name: str = Field(..., alias="trustName")
    trust_id: str = Field(..., alias="trustId")
    data: List[Results]

    model_config = ConfigDict(
        populate_by_name=True,
    )


class OMOPResult(BaseModel):
    """Model for OMOP results."""

    name: str
    results: List[OMOPTrustResults]


class OmopCohortResultsResponse(BaseModel):
    """Response model for OMOP cohort results."""

    record_count: int = Field(..., alias="recordCount")
    trusts_results: List[OMOPResult] = Field(..., alias="trustsResults")

    model_config = ConfigDict(
        populate_by_name=True,
    )


class OMOPDbResult(BaseModel):
    """Model for OMOP database results."""

    stats: str


# Additional models from the first part of the file
class SubmitCohortQuery(BaseModel):
    """Model for submitting a cohort query."""

    authenticationToken: str = Field(..., description="This is passed across to the trusts for authentication")
    query: str
    name: str
    project_id: UUID
    query_id: UUID


class SubmitCohortQueryBody(BaseModel):
    """Model for the body of a cohort query submission."""

    query: str
    query_name: str
    encrypted_project_id: str
    query_id: UUID
    trust_id: str


class TrustDetails(BaseModel):
    """Model for trust details in cohort query submission."""

    name: str
    statusCode: int
    message: Optional[str] = None


class SubmitCohortQueryOutput(BaseModel):
    """Output model for cohort query submission."""

    trust: List[TrustDetails]
    query_id: UUID = Field(..., alias="queryId")

    model_config = ConfigDict(
        populate_by_name=True,
    )


class CohortQueryInput(BaseModel):
    """Input model for cohort query submission."""

    query: str
    name: str
    project_id: UUID = Field(..., alias="projectId")

    model_config = ConfigDict(
        populate_by_name=True,
    )


class SubmitCohortQueryInput(CohortQueryInput):
    """Input model for submitting a cohort query with authentication token."""

    authenticationToken: str
    query_id: UUID
