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

"""The model behind cohort SQL drafting: a small interface, a canned fake and Claude on Amazon Bedrock.

The interface exists for the fake (tests, development, the UI mock), not to support other providers.
"""

import json
import os
import re
from dataclasses import dataclass
from functools import cache
from typing import Any, Protocol

import anthropic
from anthropic import AnthropicBedrockMantle

from flip_api.cohort_services.drafting.prompt import ASSETS, OUTPUT_SCHEMA, strip_licence_header
from flip_api.config import get_settings

# Bedrock Guardrail headers, as on bedrock-runtime InvokeModel. Not yet verified on the Mantle endpoint:
# confirm before attaching a guardrail in phase 2 (FLIP#1556).
GUARDRAIL_ID_HEADER = "X-Amzn-Bedrock-GuardrailIdentifier"
GUARDRAIL_VERSION_HEADER = "X-Amzn-Bedrock-GuardrailVersion"

# Environment variables that would switch the Mantle client from SigV4 to a Bedrock API key.
_API_KEY_ENV_VARS = ("AWS_BEARER_TOKEN_BEDROCK", "ANTHROPIC_AWS_API_KEY")


class ModelError(Exception):
    """The model call failed (timeout, throttling, unavailable). The message is a short code, safe to log."""


@dataclass
class ModelReply:
    """One model response, reduced to what the drafting loop needs."""

    stop_reason: str | None
    text: str
    # The assistant content as returned, replayed unchanged on a retry (thinking blocks included).
    content: Any
    input_tokens: int = 0
    output_tokens: int = 0
    refusal_category: str | None = None


class DraftModel(Protocol):
    """Anything that answers the drafting prompt."""

    name: str

    def create(self, system: str, messages: list[dict[str, Any]]) -> ModelReply:
        """Run one call. Raises ModelError on failure."""
        ...


class FakeDraftModel:
    """Deterministic canned replies, keyed on keywords: never calls a model.

    A request to change data or for identifiers is declined; a tabular project asked for imaging is
    declined; an imaging description naming no modality is asked which one, once; anything else gets the
    canned SQL for the project's shape, whatever it describes.
    """

    name = "fake"

    def create(self, system: str, messages: list[dict[str, Any]]) -> ModelReply:
        text = " ".join(m["content"] for m in messages if m["role"] == "user" and isinstance(m["content"], str))
        lowered = text.lower()
        asked = _between(lowered, "<description>", "</description>")
        imaging = "project shape: imaging" in lowered
        reply: dict[str, Any] = {
            "outcome": "draft",
            "sql": "",
            "explanation": "",
            "assumptions": [],
            "concepts_used": [],
            "question": "",
            "options": [],
        }
        if re.search(r"\b(delete|drop|update|insert|names?|nhs numbers?)\b", asked):
            reply |= {"outcome": "decline", "explanation": "Drafts are read-only cohorts with no direct identifiers."}
        elif not imaging and re.search(r"x-?rays?|\bct\b|\bmri\b|\bscans?\b|\bimag", asked):
            reply |= {
                "outcome": "decline",
                "explanation": "This is a tabular-only project, so a draft cannot select imaging studies.",
            }
        elif imaging and not re.search(r"x-?ray|\bct\b|\bmri\b|<clarifications>", lowered):
            reply |= {"outcome": "clarification", "question": "Which modality?", "options": ["X-ray", "CT", "MRI"]}
        else:
            reply |= {
                "sql": fake_sql(imaging),
                "explanation": "A canned draft from the development drafter: it does not follow your description.",
                "assumptions": ["The development drafter returns the same query for every description."],
                "concepts_used": ["4056681 = plain radiography", "4300757 = CT", "4013636 = MRI"] if imaging else [],
            }
        return ModelReply(stop_reason="end_turn", text=json.dumps(reply), content=json.dumps(reply))


def _between(text: str, opening: str, closing: str) -> str:
    """The text between the first ``opening`` and the next ``closing``, else all of it (linear time)."""
    start = text.find(opening)
    if start != -1:
        end = text.find(closing, start + len(opening))
        if end != -1:
            return text[start + len(opening) : end]
    return text


@cache
def fake_sql(imaging: bool) -> str:
    """The fake drafter's canned SQL for a project shape."""
    return strip_licence_header((ASSETS / "fake" / ("imaging.sql" if imaging else "tabular.sql")).read_text())


class BedrockDraftModel:
    """Claude on Amazon Bedrock, through the official SDK's Mantle client and the ECS task role.

    No server-side refusal fallback: the ``fallbacks`` parameter is not offered on Amazon Bedrock, and the
    SDK's client-side fallback would send the description to a second model nobody has reviewed. A
    refusal is returned to the researcher as a decline instead.
    """

    name = "bedrock"

    def __init__(self, client: Any = None) -> None:
        settings = get_settings()
        self._model = settings.COHORT_DRAFT_BEDROCK_MODEL_ID
        self._max_tokens = settings.COHORT_DRAFT_MAX_TOKENS
        self._effort = settings.COHORT_DRAFT_EFFORT
        self._headers: dict[str, str] = {}
        if settings.COHORT_DRAFT_GUARDRAIL_ID and settings.COHORT_DRAFT_GUARDRAIL_VERSION:
            self._headers = {
                GUARDRAIL_ID_HEADER: settings.COHORT_DRAFT_GUARDRAIL_ID,
                GUARDRAIL_VERSION_HEADER: settings.COHORT_DRAFT_GUARDRAIL_VERSION,
            }
        self._client: Any = client if client is not None else _bedrock_client()

    def create(self, system: str, messages: list[dict[str, Any]]) -> ModelReply:
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                # Static per release, so it is cached across requests (Bedrock supports prompt caching).
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=messages,
                output_config={"effort": self._effort, "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
                extra_headers=self._headers or None,
            )
        except anthropic.APITimeoutError as e:
            raise ModelError("timeout") from e
        except anthropic.RateLimitError as e:
            raise ModelError("throttled") from e
        except anthropic.APIStatusError as e:
            raise ModelError(f"status_{e.status_code}") from e
        except anthropic.APIConnectionError as e:
            raise ModelError("connection") from e
        category = None
        if response.stop_reason == "refusal" and response.stop_details is not None:
            category = response.stop_details.category
        return ModelReply(
            stop_reason=response.stop_reason,
            text=next((b.text for b in response.content if b.type == "text"), ""),
            content=response.content,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            refusal_category=category,
        )


@cache
def _bedrock_client() -> AnthropicBedrockMantle:
    """One Mantle client per process, signed with the default AWS credential chain (the ECS task role)."""
    if any(os.environ.get(name) for name in _API_KEY_ENV_VARS):
        raise ModelError("api_key_configured")  # the hub calls Bedrock with its task role only
    settings = get_settings()
    return AnthropicBedrockMantle(
        aws_region=settings.COHORT_DRAFT_BEDROCK_REGION,
        timeout=settings.COHORT_DRAFT_TIMEOUT_SECONDS,
        max_retries=1,
    )


def get_draft_model() -> DraftModel:
    """The configured model: canned in development, Bedrock in stag/prod."""
    return BedrockDraftModel() if get_settings().COHORT_DRAFT_PROVIDER == "bedrock" else FakeDraftModel()
