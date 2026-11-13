# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
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

"""Request and response bodies of the cohort SQL drafting routes (FLIP#1556, Beta)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from flip_api.cohort_services.drafting.prompt import MAX_CLARIFICATIONS


class CohortDraftClarification(BaseModel):
    """A question the assistant asked, and the researcher's answer."""

    question: str = Field(max_length=500)
    answer: str = Field(min_length=1, max_length=500)


class CohortDraftRequestBody(BaseModel):
    """POST /cohort/draft. The project's shape is read server-side, never taken from the client."""

    project_id: UUID
    description: str = Field(min_length=1)
    clarifications: list[CohortDraftClarification] = Field(default_factory=list, max_length=MAX_CLARIFICATIONS)


class CohortDraftUsage(BaseModel):
    """The caller's monthly allowance. Only a validated draft is charged."""

    enabled: bool
    used: int
    limit: int
    remaining: int
    resets_at: datetime


class CohortDraftResponse(BaseModel):
    """A checked draft, a clarification question, a decline, or an error; always with the allowance."""

    status: Literal["draft", "clarification", "decline", "error"]
    sql: str | None = None
    explanation: str | None = None
    assumptions: list[str] = Field(default_factory=list)
    concepts_used: list[str] = Field(default_factory=list)
    question: str | None = None
    options: list[str] = Field(default_factory=list)
    # For "error": allowance_exhausted, rate_limited, duplicate_request, model_unavailable or invalid_draft.
    error_code: str | None = None
    message: str | None = None
    usage: CohortDraftUsage
