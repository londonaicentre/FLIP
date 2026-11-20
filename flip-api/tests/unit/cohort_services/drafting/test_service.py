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

from flip_api.cohort_services.drafting.model import ModelError, ModelReply
from flip_api.cohort_services.drafting.prompt import MAX_ITEM_CHARS, MAX_ITEMS, MAX_OPTIONS, MAX_TEXT_CHARS
from flip_api.cohort_services.drafting.service import GUARDRAIL_MESSAGE, MAX_MODEL_CALLS, REFUSAL_MESSAGE, run_draft

GOOD = "SELECT p.person_id FROM omop.person p"
BAD = "SELECT p.person_id, p.nhs_number FROM omop.person p"


def _out(outcome="draft", sql=GOOD, **kw) -> str:
    base = {"outcome": outcome, "sql": sql, "explanation": "e", "assumptions": ["a"], "concepts_used": []}
    return json.dumps(base | {"question": "", "options": []} | kw)


class Scripted:
    """A model that answers from a list and records what it was sent."""

    name = "scripted"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def create(self, system, messages):
        self.calls.append([dict(m) for m in messages])
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, ModelReply):
            return reply
        return ModelReply(stop_reason="end_turn", text=reply, content=reply, input_tokens=10, output_tokens=5)


def test_valid_draft_first_time():
    outcome = run_draft(Scripted(_out()), "d", [], has_imaging=False)
    assert (outcome.status, outcome.sql, outcome.retries, outcome.assumptions) == ("draft", GOOD, 0, ["a"])


def test_rejection_is_fed_back_and_retried():
    model = Scripted(_out(sql=BAD), _out())
    outcome = run_draft(model, "d", [], has_imaging=False)
    assert (outcome.status, outcome.retries, outcome.input_tokens) == ("draft", 1, 20)
    retry = model.calls[1]
    assert retry[1] == {"role": "assistant", "content": _out(sql=BAD)}
    assert "Unknown column: nhs_number" in retry[2]["content"]


def test_gives_up_after_two_retries():
    model = Scripted(_out(sql=BAD), _out(sql=BAD), _out(sql=BAD))
    outcome = run_draft(model, "d", [], has_imaging=False)
    assert (outcome.status, outcome.error_code, len(model.calls)) == ("error", "invalid_draft", 3)
    assert outcome.model_calls == MAX_MODEL_CALLS == 3


def test_every_model_call_is_counted():
    assert run_draft(Scripted(_out(sql=BAD), _out()), "d", [], False).model_calls == 2
    assert run_draft(Scripted(ModelError("timeout")), "d", [], False).model_calls == 1


def test_oversized_model_text_is_cut():
    long = "x" * 5000
    draft = run_draft(Scripted(_out(explanation=long, assumptions=[long] * 30, concepts_used=[long])), "d", [], False)
    assert len(draft.explanation) == MAX_TEXT_CHARS
    assert len(draft.assumptions) == MAX_ITEMS
    assert {len(a) for a in draft.assumptions + draft.concepts_used} == {MAX_ITEM_CHARS}
    clarification = run_draft(Scripted(_out("clarification", "", question=long, options=[long] * 9)), "d", [], True)
    assert len(clarification.question) == MAX_ITEM_CHARS
    assert [len(o) for o in clarification.options] == [MAX_ITEM_CHARS] * MAX_OPTIONS
    decline = run_draft(Scripted(_out("decline", "", explanation=long)), "d", [], False)
    assert len(decline.explanation) == MAX_TEXT_CHARS


def test_output_contract_follows_the_shape():
    outcome = run_draft(Scripted(_out(), _out(), _out()), "d", [], has_imaging=True)
    assert outcome.error_code == "invalid_draft"


def test_refusal_is_a_decline():
    reply = ModelReply(stop_reason="refusal", text="", content=[], refusal_category="general_harms")
    outcome = run_draft(Scripted(reply), "d", [], has_imaging=False)
    assert (outcome.status, outcome.explanation, outcome.refusal_category) == (
        "decline",
        REFUSAL_MESSAGE,
        "general_harms",
    )


def test_model_decline_and_clarification():
    assert run_draft(Scripted(_out("decline", "", explanation="no")), "d", [], False).explanation == "no"
    clarification = run_draft(Scripted(_out("clarification", "", question="CT?", options=list("abcde"))), "d", [], True)
    assert (clarification.status, clarification.question, clarification.options) == (
        "clarification",
        "CT?",
        list("abcd"),
    )


def test_failures_are_errors():
    truncated = ModelReply(stop_reason="max_tokens", text="", content=[])
    assert run_draft(Scripted(truncated), "d", [], False).error_code == "stop_max_tokens"
    assert run_draft(Scripted("not json"), "d", [], False).error_code == "malformed_output"
    assert run_draft(Scripted(_out("clarification", "")), "d", [], False).error_code == "malformed_output"
    assert run_draft(Scripted(ModelError("timeout")), "d", [], False).error_code == "model_timeout"


def test_unflagged_code_off_the_safe_list_is_fed_back():
    guessed = _out(concepts_used=["201826 = type 2 diabetes"], assumptions=["a"])
    flagged = _out(concepts_used=["201826 = type 2 diabetes"], assumptions=["201826 is unverified"])
    model = Scripted(guessed, flagged)
    outcome = run_draft(model, "d", [], has_imaging=False)
    assert (outcome.status, outcome.retries) == ("draft", 1)
    assert "201826 is not on the safe list" in model.calls[1][2]["content"]


def test_guardrail_block_is_a_decline():
    reply = ModelReply(stop_reason="guardrail_intervened", text="", content=[])
    outcome = run_draft(Scripted(reply), "d", [], has_imaging=False)
    assert (outcome.status, outcome.explanation, outcome.model_calls) == ("decline", GUARDRAIL_MESSAGE, 1)
