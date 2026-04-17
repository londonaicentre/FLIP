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
import re
from uuid import UUID

# SQL parser library - would need Python equivalent
# For this example using sqlparse, but may need a more robust solution
import sqlparse  # type: ignore[import]
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlmodel import Session, select

from flip_api.auth.access_manager import can_modify_project
from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.db.models.main_models import Trust, TrustTask
from flip_api.domain.schemas.cohort import (
    SubmitCohortQuery,
    SubmitCohortQueryBody,
    SubmitCohortQueryOutput,
    TrustDetails,
)
from flip_api.domain.schemas.status import TaskType
from flip_api.utils.encryption import encrypt
from flip_api.utils.logger import logger

router = APIRouter(prefix="/cohort", tags=["cohort_services"])


FORBIDDEN_COMMANDS = [
    "alter user",
    "alter table",
    "alter database",
    "drop table",
    "drop user",
    "drop role",
    "drop database",
    "create table",
    "substring",
]

# Match any occurrence of any forbidden commands
REGEX = re.compile(f"({'|'.join(FORBIDDEN_COMMANDS)})", re.IGNORECASE)


def contains_forbidden_commands(query: str) -> bool:
    """
    Check if the query contains any forbidden commands

    Args:
        query: SQL query string

    Returns:
        bool: True if the query contains forbidden commands, False otherwise
    """
    return bool(REGEX.search(query))


def validate_query(query: str) -> None:
    """
    Validate the SQL query syntax

    Args:
        query: SQL query string

    Raises:
        ValueError: If the query is not valid SQL
    """
    try:
        # Use sqlparse to verify SQL validity
        # This is a simplified version - you might need a more robust parser
        # TODO: Replace with a more robust SQL parser if needed.
        # TODO: Create tests to veryfy if sql injection can be done
        parsed = sqlparse.parse(query)
        if not parsed:
            raise ValueError("Empty or invalid SQL")

        logger.info("Query is valid SQL")
    except Exception as e:
        logger.error({"message": "Query is not valid SQL.", "error": str(e)})
        raise ValueError("Invalid SQL Query")


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

        # Additional validation
        if contains_forbidden_commands(cohort_query.query):
            raise HTTPException(status_code=400, detail="Invalid query: Contains forbidden SQL commands")

        # Validate SQL syntax
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
                    payload=json.dumps(task_payload.model_dump(mode="json")),
                )
                db.add(task)

                result.append(
                    TrustDetails(
                        name=trust.name,
                        statusCode=202,
                        message="Task queued",
                    )
                )

            except Exception as e:
                logger.error(
                    f"Unable to queue cohort query task for trust {trust.name}: {str(e)}"
                )
                result.append(TrustDetails(name=trust.name, statusCode=500, message=str(e)))

            logger.info(f"Trust: {trust.name} processed")

        db.commit()

        # Prepare response
        data_to_return = SubmitCohortQueryOutput(trust=result, query_id=cohort_query.query_id)  # type: ignore[call-arg]

        logger.info("Successfully queued cohort query tasks for all trusts")

        return data_to_return

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error submitting cohort query: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
