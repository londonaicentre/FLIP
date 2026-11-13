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

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import anthropic
import httpx2
import pytest

from flip_api.cohort_services.drafting import model as model_module
from flip_api.cohort_services.drafting.model import (
    GUARDRAIL_ID_HEADER,
    GUARDRAIL_VERSION_HEADER,
    BedrockDraftModel,
    FakeDraftModel,
    ModelError,
    get_draft_model,
)
from flip_api.cohort_services.drafting.prompt import OUTPUT_SCHEMA, user_message
from flip_api.config import get_settings

_REQUEST = httpx2.Request("POST", "https://bedrock-mantle.eu-west-2.api.aws/anthropic/v1/messages")


def _fake(description: str, imaging: bool, clarifications=()) -> dict:
    reply = FakeDraftModel().create(
        "system", [{"role": "user", "content": user_message(description, list(clarifications), imaging)}]
    )
    assert reply.stop_reason == "end_turn"
    return json.loads(reply.text)


def _message(stop_reason="end_turn", text='{"outcome": "decline"}', stop_details=None):
    return SimpleNamespace(
        stop_reason=stop_reason,
        stop_details=stop_details,
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=1200, output_tokens=300),
    )


def test_fake_drafts_for_each_shape():
    assert "accession_id" in _fake("Chest X-rays of adults", imaging=True)["sql"]
    tabular = _fake("Patients with type 2 diabetes", imaging=False)
    assert tabular["outcome"] == "draft"
    assert "accession_id" not in tabular["sql"]


def test_fake_asks_for_a_modality_once():
    asked = _fake("Scans of women over 60", imaging=True)
    assert asked["outcome"] == "clarification"
    assert asked["options"]
    assert _fake("Scans of women over 60", True, [("Which modality?", "CT")])["outcome"] == "draft"


@pytest.mark.parametrize(
    ("description", "imaging"),
    [("Delete the opted-out rows", True), ("All CT scans", False), ("Their NHS numbers", False)],
)
def test_fake_declines(description, imaging):
    assert _fake(description, imaging)["outcome"] == "decline"


def test_development_uses_the_fake():
    assert isinstance(get_draft_model(), FakeDraftModel)


def test_bedrock_request_shape(monkeypatch):
    client = MagicMock()
    client.messages.create.return_value = _message(text='{"outcome": "draft"}')
    reply = BedrockDraftModel(client=client).create("SYSTEM", [{"role": "user", "content": "hi"}])

    kwargs = client.messages.create.call_args.kwargs
    settings = get_settings()
    assert kwargs["model"] == settings.COHORT_DRAFT_BEDROCK_MODEL_ID == "anthropic.claude-sonnet-5-5"
    assert kwargs["max_tokens"] == settings.COHORT_DRAFT_MAX_TOKENS
    assert kwargs["system"] == [{"type": "text", "text": "SYSTEM", "cache_control": {"type": "ephemeral"}}]
    assert kwargs["output_config"] == {
        "effort": settings.COHORT_DRAFT_EFFORT,
        "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA},
    }
    assert "thinking" not in kwargs
    assert "tool_choice" not in kwargs
    assert "fallbacks" not in kwargs
    assert kwargs["extra_headers"] is None
    assert reply.text == '{"outcome": "draft"}'
    assert (reply.input_tokens, reply.output_tokens) == (1200, 300)


def test_bedrock_passes_the_guardrail_through(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "COHORT_DRAFT_GUARDRAIL_ID", "gr-123")
    monkeypatch.setattr(settings, "COHORT_DRAFT_GUARDRAIL_VERSION", "2")
    client = MagicMock()
    client.messages.create.return_value = _message()
    BedrockDraftModel(client=client).create("S", [])
    assert client.messages.create.call_args.kwargs["extra_headers"] == {
        GUARDRAIL_ID_HEADER: "gr-123",
        GUARDRAIL_VERSION_HEADER: "2",
    }


def test_bedrock_refusal_carries_its_category():
    client = MagicMock()
    client.messages.create.return_value = _message(
        stop_reason="refusal", text="", stop_details=SimpleNamespace(category="general_harms")
    )
    reply = BedrockDraftModel(client=client).create("S", [])
    assert (reply.stop_reason, reply.refusal_category) == ("refusal", "general_harms")


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (anthropic.APITimeoutError(request=_REQUEST), "timeout"),
        (anthropic.RateLimitError("x", response=httpx2.Response(429, request=_REQUEST), body=None), "throttled"),
        (anthropic.InternalServerError("x", response=httpx2.Response(503, request=_REQUEST), body=None), "status_503"),
        (anthropic.APIConnectionError(request=_REQUEST), "connection"),
    ],
)
def test_bedrock_errors_become_model_errors(error, code):
    client = MagicMock()
    client.messages.create.side_effect = error
    with pytest.raises(ModelError, match=code):
        BedrockDraftModel(client=client).create("S", [])


def test_bedrock_client_refuses_an_api_key(monkeypatch):
    model_module._bedrock_client.cache_clear()
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "not-allowed")
    with pytest.raises(ModelError, match="api_key_configured"):
        model_module._bedrock_client()
    model_module._bedrock_client.cache_clear()


def test_bedrock_client_signs_with_the_task_role(monkeypatch):
    model_module._bedrock_client.cache_clear()
    for name in ("AWS_BEARER_TOKEN_BEDROCK", "ANTHROPIC_AWS_API_KEY", "ANTHROPIC_BEDROCK_MANTLE_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    client = model_module._bedrock_client()
    model_module._bedrock_client.cache_clear()
    assert client.api_key is None
    assert str(client.base_url).startswith("https://bedrock-mantle.eu-west-2.api.aws/anthropic")
