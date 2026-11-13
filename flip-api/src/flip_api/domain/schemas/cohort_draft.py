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
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from flip_api.cohort_services.drafting.prompt import (
    MAX_CLARIFICATION_CHARS,
    MAX_CLARIFICATIONS,
    MAX_ITEM_CHARS,
    MAX_ITEMS,
    MAX_OPTIONS,
    MAX_TEXT_CHARS,
)

_Item = Annotated[str, Field(max_length=MAX_ITEM_CHARS)]


class CohortDraftClarification(BaseModel):
    """A question the assistant asked, and the researcher's answer."""

    question: str = Field(max_length=MAX_CLARIFICATION_CHARS)
    answer: str = Field(min_length=1, max_length=MAX_CLARIFICATION_CHARS)


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
    explanation: str | None = Field(default=None, max_length=MAX_TEXT_CHARS)
    assumptions: list[_Item] = Field(default_factory=list, max_length=MAX_ITEMS)
    concepts_used: list[_Item] = Field(default_factory=list, max_length=MAX_ITEMS)
    question: str | None = Field(default=None, max_length=MAX_ITEM_CHARS)
    options: list[_Item] = Field(default_factory=list, max_length=MAX_OPTIONS)
    # For "error": allowance_exhausted, request_cap_reached, rate_limited, duplicate_request,
    # model_unavailable or invalid_draft.
    error_code: str | None = None
    message: str | None = Field(default=None, max_length=MAX_TEXT_CHARS)
    usage: CohortDraftUsage


_Reason = Annotated[str, Field(min_length=1, max_length=500)]


class CohortDraftOverrideBody(BaseModel):
    """PUT /cohort/draft/overrides/{user_id}: one user's monthly limits, until ``expires_at`` (UTC) if given."""

    monthly_allowance: int = Field(ge=0, le=10_000)
    # None keeps the configured COHORT_DRAFT_MONTHLY_REQUEST_CAP.
    monthly_request_cap: int | None = Field(default=None, ge=0, le=100_000)
    expires_at: datetime | None = None
    reason: _Reason


class CohortDraftOverrideClearBody(BaseModel):
    """DELETE /cohort/draft/overrides/{user_id}."""

    reason: _Reason


class CohortDraftOverrideView(BaseModel):
    """A user's override, and whether it is in force now."""

    user_id: UUID
    monthly_allowance: int
    monthly_request_cap: int | None
    expires_at: datetime | None
    active: bool
    updated_by: UUID
    updated_at: datetime
