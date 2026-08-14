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

import json
from uuid import UUID

import sqlglot
import sqlglot.expressions
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlglot.errors import SqlglotError
from sqlmodel import Session, select

from flip_api.auth.access_manager import can_modify_project
from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.db.models.main_models import Queries, Trust, TrustTask
from flip_api.domain.schemas.cohort import (
    SubmitCohortQuery,
    SubmitCohortQueryBody,
    SubmitCohortQueryOutput,
    TrustDetails,
)
from flip_api.domain.schemas.status import TaskType
from flip_api.utils.encryption import encrypt
from flip_api.utils.log_hygiene import hash_query
from flip_api.utils.logger import logger

router = APIRouter(prefix="/cohort", tags=["cohort_services"])


# Reject pathologically large queries before the parser allocates an AST. Kept
# equal to the trust-side MAX_QUERY_LENGTH so the hub does not accept a query
# that every trust would then refuse.
MAX_QUERY_LENGTH = 10_240  # 10 KiB

# Top-level statement shapes that count as SELECT-like for the cohort API.
_SELECT_LIKE_STATEMENTS: tuple[type[sqlglot.expressions.Expression], ...] = (
    sqlglot.expressions.Select,
    sqlglot.expressions.Union,
    sqlglot.expressions.Intersect,
    sqlglot.expressions.Except,
)


def validate_query(query: str) -> None:
    """
    Fast-feedback validity pre-check for a cohort query. **Not a security control.**

    Why this exists at all
    ----------------------
    The trust-side ``data-access-api`` is the *authority* on what a cohort query
    may do (see ``data_access_api.services.cohort.validate_query``), and it is
    deliberately self-sufficient: a trust holds patient data and must stay safe
    regardless of what the hub did or did not check, because the hub is a
    separate administrative domain that the trust does not trust. Anything this
    function enforced for safety would therefore have to be enforced trust-side
    anyway, so duplicating the trust's rules here would buy no security — only
    two copies of one policy to keep in sync.

    What it buys instead is **fast feedback**. Submitting a cohort query fans it
    out to every registered trust as an encrypted task, each of which runs its
    own validation asynchronously and reports back. Without a pre-check, a
    researcher who typos their SQL waits for that whole round-trip, across N
    trusts, to be told. Catching "this cannot possibly succeed anywhere" while
    the request is still in-hand turns a multi-minute fan-out into a 400.

    The asymmetry is intentional and safe by construction
    -----------------------------------------------------
    This check is deliberately *weaker* than the trust's, and only ever rejects
    queries that every trust would reject too. It intentionally does **not**
    enforce trust-local policy — the ``omop`` schema pin, literal-``LIMIT``
    rule, read-only role, or minimum-cohort threshold — because those depend on
    facts the hub has no authority over and which may legitimately differ per
    trust.

    That makes drift between the two harmless in the direction that matters: a
    hub lagging behind the trust merely wastes a fan-out on a query the trust
    then refuses, never a bypass. Do not "fix" the asymmetry by copying the
    trust's rules in here.

    Args:
        query (str): The SQL query string submitted by the researcher.

    Raises:
        ValueError: If the query is over length, unparseable, not exactly one
            statement, or not SELECT-shaped.
    """
    if len(query) > MAX_QUERY_LENGTH:
        raise ValueError(f"Query exceeds maximum length of {MAX_QUERY_LENGTH} characters.")

    try:
        statements = sqlglot.parse(query, read="postgres")
    except SqlglotError as e:
        # SqlglotError covers both ParseError and TokenError (e.g. an
        # unterminated string literal), so a tokenizer failure cannot bubble up
        # as an unhandled 500. Class + fingerprint only: sqlglot interpolates
        # the offending SQL fragment into its message (logging policy).
        logger.info({
            "message": "Cohort query rejected: could not be parsed as SQL.",
            "error": type(e).__name__,
            "query_sha256": hash_query(query),
        })
        raise ValueError("Query could not be parsed as SQL.") from e

    # sqlglot yields None for empty input and for stray semicolons (``SELECT 1; ;``
    # parses to ``[Select, None]``), so filtering None out would let a caller
    # smuggle an extra statement past the count check.
    if len(statements) != 1 or statements[0] is None:
        raise ValueError("Exactly one SQL statement is allowed per request.")

    if not isinstance(statements[0], _SELECT_LIKE_STATEMENTS):
        raise ValueError("Only SELECT statements are allowed.")


# TODO [#114] This endpoint was not defined in the old repo. The old repo defined a step function that ran the
# following steps: (1) getProject, (2) save_cohort_query, (3) submit_cohort_query
@router.post("/submit", response_model=SubmitCohortQueryOutput)
def submit_cohort_query(
    request: Request,
    cohort_query: SubmitCohortQuery = Body(...),
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
) -> SubmitCohortQueryOutput:
    """
    Submit a cohort query to all available trusts.

    Args:
        request (Request): HTTP request object.
        cohort_query (SubmitCohortQuery): Query details payload.
        db (Session): Database session.
        user_id (UUID): ID of the authenticated user.

    Returns:
        SubmitCohortQueryOutput: The result of the submission to each trust

    Raises:
        HTTPException: If the query contains forbidden commands, if the SQL syntax is invalid, if no trusts are found,
        or if there is an error communicating with the trusts.
    """
    try:
        if not can_modify_project(user_id, cohort_query.project_id, db):
            raise HTTPException(
                status_code=403,
                detail=f"User with ID: {user_id} is not allowed to modify this project",
            )

        # Fast-feedback validity pre-check only — the trust is the authority on
        # query safety. See validate_query's docstring for why this is
        # deliberately weaker than the trust-side check.
        try:
            validate_query(cohort_query.query)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        # Query database for trusts
        statement = select(Trust)
        trusts = db.exec(statement).all()

        if not trusts:
            logger.error("No trusts found")
            raise HTTPException(status_code=404, detail="No trusts found")

        logger.info(f"Trusts found: {len(trusts)}")

        result: list[TrustDetails] = []
        queried_trust_ids: list[UUID] = []

        # Encrypt project_id before sending to trusts
        encrypted_project_id = encrypt(str(cohort_query.project_id))
        logger.debug("Checking if project_id is encrypted: %s", encrypted_project_id)

        # Queue a task for each trust (instead of direct HTTP calls)
        for trust in trusts:
            try:
                task_payload = SubmitCohortQueryBody(
                    query_name=cohort_query.name,
                    query=cohort_query.query,
                    encrypted_project_id=encrypted_project_id,
                    query_id=cohort_query.query_id,
                    trust_id=str(trust.id),
                )

                task = TrustTask(
                    trust_id=trust.id,
                    task_type=TaskType.COHORT_QUERY,
                    query_id=cohort_query.query_id,
                    payload=json.dumps(task_payload.model_dump(mode="json")),
                )
                db.add(task)
                queried_trust_ids.append(trust.id)

                result.append(
                    TrustDetails(
                        name=trust.name,
                        statusCode=202,
                        message="Task queued",
                    )
                )

            except Exception as e:
                # Per-trust failures are isolated: the bad trust is reported with a 500 in its
                # own result entry and the batch keeps queuing the rest. db.add is in-memory, so
                # there is nothing to roll back here; a failure at db.commit() below is handled by
                # the outer except (which rolls back the whole submit).
                logger.error(
                    f"Unable to queue cohort query task for trust {trust.name}: {str(e)}"
                )
                result.append(TrustDetails(name=trust.name, statusCode=500, message=str(e)))

            logger.info(f"Trust: {trust.name} processed")

        # Persist the queried set on the Queries row so the per-trust UI
        # can surface trusts that never responded — without this, a trust that
        # was sent the query but failed to post any result (offline, hub
        # rejected the error report) would silently vanish from the panel.
        query_row = db.exec(select(Queries).where(Queries.id == cohort_query.query_id)).first()
        if query_row is not None:
            query_row.queried_trust_ids = queried_trust_ids
        else:
            # No Queries row means save_cohort_query never persisted (or was rolled back). The
            # per-trust tasks are still queued, but queried_trust_ids stays empty, so the
            # cohort-results UI can't surface trusts that never responded. Surface the upstream
            # gap instead of committing silently.
            logger.warning(
                "No Queries row for query_id %s; cohort tasks were queued but queried_trust_ids was not persisted.",
                cohort_query.query_id,
            )

        db.commit()

        # Prepare response
        data_to_return = SubmitCohortQueryOutput(trust=result, query_id=cohort_query.query_id)  # type: ignore[call-arg]

        logger.info("Successfully queued cohort query tasks for all trusts")

        return data_to_return

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        # Class-only: a DB error on this path renders the INSERT whose bound
        # parameters include the raw cohort SQL (logging policy).
        logger.error(f"Error submitting cohort query: {type(e).__name__}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
