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

"""The drafting loop: one model call, the checks, and at most two retries with the rejection fed back."""

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from flip_api.cohort_services.drafting.checks import check_concepts, check_draft
from flip_api.cohort_services.drafting.model import DraftModel, ModelError
from flip_api.cohort_services.drafting.prompt import (
    MAX_ITEM_CHARS,
    MAX_ITEMS,
    MAX_OPTIONS,
    MAX_TEXT_CHARS,
    rejection_message,
    system_prompt,
    user_message,
)

MAX_VALIDATION_RETRIES = 2
# The most model calls one request can make; the ledger reserves this many up front.
MAX_MODEL_CALLS = MAX_VALIDATION_RETRIES + 1

REFUSAL_MESSAGE = "The assistant declined this description. Write the SQL by hand, or describe the cohort differently."
GUARDRAIL_MESSAGE = (
    "The description was blocked because it looks like it contains personal details, such as a name or an NHS "
    "number. Describe the cohort without them."
)


class _ModelOutput(BaseModel):
    outcome: Literal["draft", "clarification", "decline"]
    sql: str
    explanation: str
    assumptions: list[str]
    concepts_used: list[str]
    question: str
    options: list[str]


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _clip_items(items: list[str], count: int = MAX_ITEMS) -> list[str]:
    return [_clip(i, MAX_ITEM_CHARS) for i in items[:count]]


@dataclass
class DraftOutcome:
    """What the loop produced, plus metadata for the log. ``error_code`` is set only for ``error``."""

    status: Literal["draft", "clarification", "decline", "error"]
    sql: str = ""
    explanation: str = ""
    assumptions: list[str] = field(default_factory=list)
    concepts_used: list[str] = field(default_factory=list)
    question: str = ""
    options: list[str] = field(default_factory=list)
    error_code: str | None = None
    retries: int = 0
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    refusal_category: str | None = None


def run_draft(
    model: DraftModel, description: str, clarifications: list[tuple[str, str]], has_imaging: bool
) -> DraftOutcome:
    """Draft a cohort query and check it. Never runs SQL.

    Args:
        model (DraftModel): The model to ask.
        description (str): The researcher's description.
        clarifications (list[tuple[str, str]]): Earlier (question, answer) pairs.
        has_imaging (bool): The project's shape, which sets the output contract.

    Returns:
        DraftOutcome: A checked draft, a clarification question, a decline, or an error.
    """
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": user_message(description, clarifications, has_imaging)}
    ]
    outcome = DraftOutcome(status="error")
    for attempt in range(MAX_VALIDATION_RETRIES + 1):
        outcome.retries = attempt
        outcome.model_calls += 1
        try:
            reply = model.create(system_prompt(), messages)
        except ModelError as e:
            outcome.error_code = f"model_{e}"
            return outcome
        outcome.input_tokens += reply.input_tokens
        outcome.output_tokens += reply.output_tokens
        # stop_reason comes first: a refused or truncated reply has no usable content.
        if reply.stop_reason == "refusal":
            outcome.status, outcome.explanation = "decline", REFUSAL_MESSAGE
            outcome.refusal_category = reply.refusal_category
            return outcome
        if reply.stop_reason == "guardrail_intervened":
            outcome.status, outcome.explanation = "decline", GUARDRAIL_MESSAGE
            return outcome
        if reply.stop_reason != "end_turn":
            outcome.error_code = f"stop_{reply.stop_reason}"
            return outcome
        try:
            out = _ModelOutput.model_validate(json.loads(reply.text))
        except (json.JSONDecodeError, ValidationError):
            outcome.error_code = "malformed_output"
            return outcome
        if out.outcome == "decline":
            outcome.status, outcome.explanation = "decline", _clip(out.explanation, MAX_TEXT_CHARS)
            return outcome
        if out.outcome == "clarification":
            if not out.question.strip():
                outcome.error_code = "malformed_output"
                return outcome
            outcome.status = "clarification"
            outcome.question = _clip(out.question, MAX_ITEM_CHARS)
            outcome.options = _clip_items(out.options, MAX_OPTIONS)
            return outcome
        problem = check_draft(out.sql, has_imaging) or check_concepts(out.concepts_used, out.assumptions)
        if problem is None:
            outcome.status = "draft"
            outcome.sql, outcome.explanation = out.sql.strip(), _clip(out.explanation, MAX_TEXT_CHARS)
            outcome.assumptions, outcome.concepts_used = _clip_items(out.assumptions), _clip_items(out.concepts_used)
            return outcome
        messages += [
            {"role": "assistant", "content": reply.content},
            {"role": "user", "content": rejection_message(problem)},
        ]
    outcome.error_code = "invalid_draft"
    return outcome
