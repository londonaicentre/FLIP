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

"""The versioned drafting prompt, assembled from the files in ``assets/``.

The system prompt is static per release (rules, schema, safe concept ids, examples), so the model call
can mark it for prompt caching. Adding an example is adding a file under ``assets/examples/``. Bump
:data:`PROMPT_VERSION` with any change to ``assets/``; it is logged with every draft.
"""

import re
from functools import cache
from pathlib import Path

from flip_api.cohort_services.drafting.omop_schema import load_schema, summary

PROMPT_VERSION = "2026-11-13.1"

ASSETS = Path(__file__).parent / "assets"
MAX_CLARIFICATIONS = 3
MAX_CLARIFICATION_CHARS = 500
# Caps on text returned to the researcher; longer model output is cut, and the response schema enforces them.
MAX_TEXT_CHARS = 2000
MAX_ITEM_CHARS = 500
MAX_ITEMS = 20
MAX_OPTIONS = 4

# Structured output: one object, every field always present. ``outcome`` says which fields matter.
OUTPUT_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["outcome", "sql", "explanation", "assumptions", "concepts_used", "question", "options"],
    "properties": {
        "outcome": {"type": "string", "enum": ["draft", "clarification", "decline"]},
        "sql": {"type": "string"},
        "explanation": {"type": "string"},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "concepts_used": {"type": "array", "items": {"type": "string"}},
        "question": {"type": "string"},
        "options": {"type": "array", "items": {"type": "string"}},
    },
}

_LICENCE_HEADER = re.compile(r"\A(--.*\n)+\n")
# The tags that delimit researcher text in the user turn. Hygiene only: the controls are that a draft
# is never run, and that the trust validator decides what may run.
_DELIMITER_TAG = re.compile(r"<\s*/?\s*(description|clarifications|answer|question|reply)\b[^>]*>", re.IGNORECASE)


def neutralise(text: str) -> str:
    """Remove any of the prompt's own delimiter tags from researcher text, so it cannot close its wrapper."""
    return _DELIMITER_TAG.sub("", text)


def strip_licence_header(sql: str) -> str:
    """Drop a leading ``--`` comment block followed by a blank line (the licence header)."""
    return _LICENCE_HEADER.sub("", sql).strip()


def _read(name: str) -> str:
    return (ASSETS / name).read_text().strip()


@cache
def system_prompt() -> str:
    """The static instructions: rules, schema, safe concept ids and examples."""
    examples = "\n\n".join(
        f"<example name='{p.stem}'>\n{strip_licence_header(p.read_text())}\n</example>"
        for p in sorted((ASSETS / "examples").glob("*.sql"))
    )
    schema = (
        "OMOP CDM 5.4, schema `omop` (Postgres). Exact columns and types (anything else does not exist):\n"
        f"{summary(load_schema())}\n\n{_read('schema_notes.md')}\n\n{_read('concepts.md')}"
    )
    return f"{_read('rules.md')}\n\n<schema>\n{schema}\n</schema>\n\n<examples>\n{examples}\n</examples>"


def output_contract(has_imaging: bool) -> str:
    """The output columns a project's shape requires, as the model is told."""
    if has_imaging:
        return (
            "Project shape: imaging. The outer SELECT must return person_id and accession_id "
            "(from omop.image_occurrence)."
        )
    return (
        "Project shape: tabular-only (no imaging). The outer SELECT must return person_id; do not select "
        'imaging studies. If the description asks for imaging, set outcome to "decline" and say why.'
    )


def user_message(description: str, clarifications: list[tuple[str, str]], has_imaging: bool) -> str:
    """The researcher's request, wrapped so the model treats it as data, not instructions.

    Args:
        description (str): The cohort description.
        clarifications (list[tuple[str, str]]): Earlier (question, answer) pairs, oldest first.
        has_imaging (bool): The project's shape, read server-side.

    Returns:
        str: The user turn.
    """
    parts = [
        output_contract(has_imaging),
        f"Cohort description:\n<description>\n{neutralise(description)}\n</description>",
    ]
    if clarifications:
        answers = "\n".join(
            f"<answer>\n<question>{neutralise(q)}</question>\n<reply>{neutralise(a)}</reply>\n</answer>"
            for q, a in clarifications
        )
        parts.append(f"The researcher answered your earlier questions:\n<clarifications>\n{answers}\n</clarifications>")
    return "\n\n".join(parts)


def rejection_message(problem: str) -> str:
    """The retry turn after a draft failed the hub's checks."""
    return f"FLIP's checks rejected that query: {problem}\nFix it and return the whole corrected draft."
