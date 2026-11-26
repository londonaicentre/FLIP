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

"""Checks a draft must pass before it reaches the editor. Quality checks, not a security control.

The trust's ``validate_query`` stays the authority on what may run and is deliberately not copied here
(root AGENTS.md, cohort-query validation). These checks only keep an obviously broken draft out of the
editor: the hub's existing fast pre-check, the OMOP schema, the project's output contract, the identifier
columns and the concept ids the draft says it used.
"""

import re
from functools import cache

from sqlglot import exp
from sqlglot.lineage import lineage

from flip_api.cohort_services.drafting.draft_logger import logger
from flip_api.cohort_services.drafting.omop_schema import check as check_schema
from flip_api.cohort_services.drafting.omop_schema import load_schema
from flip_api.cohort_services.drafting.prompt import ASSETS
from flip_api.cohort_services.submit_cohort_query import validate_query

# A code in a concepts_used entry, read from the text before its "=" (a concept id, SNOMED or LOINC code).
_CODE = re.compile(r"\d+(?:-\d+)?")


def required_columns(has_imaging: bool) -> tuple[str, ...]:
    """Output columns a draft must name: the trust counts subjects on them."""
    return ("person_id", "accession_id") if has_imaging else ("person_id",)


def _output_names(statement: exp.Expression) -> set[str] | None:
    """Names of the outermost SELECT's columns, or None when one is a star or has no name of its own.

    Postgres folds unquoted identifiers to lower case; quoted ones are kept as written.
    """
    node = statement.unnest()
    while isinstance(node, exp.SetOperation):  # a set operation is named by its left-most SELECT
        node = node.this.unnest()
    if not isinstance(node, exp.Select):
        return None
    names = set()
    for projection in node.expressions:
        if projection.find(exp.Star):
            return None
        identifier = projection.args.get("alias") if isinstance(projection, exp.Alias) else projection.this
        if not isinstance(projection, (exp.Alias, exp.Column)) or not isinstance(identifier, exp.Identifier):
            continue
        names.add(identifier.name if identifier.quoted else identifier.name.lower())
    return names


def _passes_through(column: str, statement: exp.Expression) -> bool:
    """Whether the output ``column`` is the same-named table column, passed unchanged through every CTE.

    Follows the column's lineage down to the tables it is read from: every step must be a plain column of
    that name, and every path must end at a table, so a literal, a cast, a computed value or another column
    renamed to it is rejected.
    """
    node = lineage(column, statement, schema={"omop": load_schema()}, dialect="postgres")
    for step in node.walk():
        if isinstance(step.expression, exp.Table):
            if step.name.rsplit(".", 1)[-1].lower() != column:
                return False
            continue
        value = step.expression.this if isinstance(step.expression, exp.Alias) else step.expression
        if not isinstance(value, exp.Column) or value.name.lower() != column or not step.downstream:
            return False
    return True


def check_identifier_columns(statement: exp.Expression, has_imaging: bool) -> str | None:
    """Check that person_id (and accession_id) are read from OMOP as they are, never computed or renamed.

    Args:
        statement (exp.Expression): The parsed draft, which already names every required column.
        has_imaging (bool): The project's shape.

    Returns:
        str | None: None when every identifier column passes, else a message the model can act on.
    """
    for column in required_columns(has_imaging):
        try:
            plain = _passes_through(column, statement)
        except Exception as e:  # a gap in sqlglot's lineage must not block SQL the trust would run
            logger.warning(f"Cohort draft identifier check skipped: {type(e).__name__}")
            return None
        if not plain:
            return (
                f"Output column {column} must be the {column} column of an OMOP table, selected as it is "
                "(through any CTE): do not compute, cast, fill or rename it."
            )
    return None


@cache
def safe_concept_ids() -> frozenset[str]:
    """The concept ids on the prompt's safe list."""
    return frozenset(_CODE.findall((ASSETS / "concepts.md").read_text()))


def check_concepts(concepts_used: list[str], assumptions: list[str]) -> str | None:
    """Check that every code the draft used outside the safe list is flagged in its assumptions.

    Args:
        concepts_used (list[str]): The draft's ``"<id> = <meaning>"`` entries.
        assumptions (list[str]): The draft's assumptions.

    Returns:
        str | None: None when every such code is named in an assumption, else a message the model can act on.
    """
    flagged = set(_CODE.findall(" ".join(assumptions)))
    unflagged = []
    for entry in concepts_used:
        for code in _CODE.findall(entry.split("=", 1)[0]):
            if code not in safe_concept_ids() and code not in flagged and code not in unflagged:
                unflagged.append(code)
    if unflagged:
        return (
            f"{', '.join(unflagged)} {'is' if len(unflagged) == 1 else 'are'} not on the safe list: add an "
            "assumption naming each one as unverified, or use a code from the safe list."
        )
    return None


def check_draft(sql: str, has_imaging: bool) -> str | None:
    """Run every check on a draft.

    Args:
        sql (str): The drafted SQL.
        has_imaging (bool): The project's shape.

    Returns:
        str | None: None when the draft passes, else a message the model can act on.
    """
    if not sql.strip():
        return "The draft has no SQL."
    try:
        statement = validate_query(sql)
    except ValueError as e:
        return str(e)
    try:
        problem = check_schema(statement, load_schema())
    except Exception as e:  # a gap in sqlglot's optimizer must not block SQL the trust would run
        logger.warning(f"Cohort draft schema check skipped: {type(e).__name__}")
        problem = None
    if problem:
        return problem
    names = _output_names(statement)
    if names is None:
        return "Name every output column of the outer SELECT; do not use *."
    missing = [c for c in required_columns(has_imaging) if c not in names]
    if missing:
        return (
            f"The outer SELECT must return {' and '.join(required_columns(has_imaging))}; missing {', '.join(missing)}."
        )
    return check_identifier_columns(statement, has_imaging)
