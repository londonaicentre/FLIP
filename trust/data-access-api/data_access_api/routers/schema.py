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

from pydantic import BaseModel, Field


class CohortQueryInput(BaseModel):
    """Represents the input for a cohort query."""

    encrypted_project_id: str = Field(
        ...,
        description="The unique identifier for the central hub project",
        json_schema_extra={"example": "12345"},
    )
    query_id: str = Field(
        ...,
        description="The unique identifier for the query",
        json_schema_extra={"example": "1"},
    )
    query_name: str = Field(
        ...,
        description="A human-readable name for the query",
        json_schema_extra={"example": "Sample Query"},
    )
    query: str = Field(
        ...,
        description="The raw SQL query to execute",
        json_schema_extra={"example": "SELECT * FROM omop.radiology_occurrence"},
    )
    trust_id: str = Field(
        ...,
        description="The unique identifier for the trust",
        json_schema_extra={"example": "trust_001"},
    )


class DataframeQuery(BaseModel):
    """Represents the input for a dataframe query."""

    encrypted_project_id: str = Field(
        ...,
        description="The encrypted identifier for the central hub project",
        json_schema_extra={"example": "encrypted_12345"},
    )
    query: str = Field(
        ...,
        description="The raw SQL query to execute",
        json_schema_extra={"example": "SELECT * FROM omop.radiology_occurrence"},
    )


class StatisticsResponse(BaseModel):
    """Represents the response for a statistics query."""

    query_id: str
    trust_id: str
    record_count: int
    created: str
    data: list[dict[str, Any]]
