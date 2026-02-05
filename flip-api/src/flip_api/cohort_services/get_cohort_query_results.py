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

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlmodel import Session, select

from flip_api.auth.access_manager import can_access_cohort_query
from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.db.models.main_models import QueryStats
from flip_api.domain.schemas.cohort import OmopCohortResultsResponse
from flip_api.utils.logger import logger

router = APIRouter(prefix="/cohort", tags=["cohort_services"])


# [#114] ✅
@router.get(
    "/{query_id}",
    response_model=OmopCohortResultsResponse,
    summary="Get cohort query results",
    description="Retrieve the results of a previously executed cohort query.",
)
def get_cohort_query_results(
    query_id: UUID = Path(..., description="Unique identifier of the cohort query"),
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
) -> OmopCohortResultsResponse:
    """
    Retrieve the results of a previously executed cohort query.

    Args:
        query_id (UUID): Unique identifier of the cohort query.
        db (Session): Database session dependency.
        user_id (UUID): ID of the user making the request, obtained from authentication.

    Returns:
        OmopCohortResultsResponse: The results of the cohort query

    Raises:
        HTTPException: If the user does not have access to the cohort query or if there are errors retrieving results.
    """
    try:
        # Check access permissions
        logger.info(f"Checking access for user: {user_id} to cohort query: {query_id}")

        if not can_access_cohort_query(user_id, query_id, db):
            logger.info(f"Access denied: User {user_id} attempted to access cohort query {query_id}")
            raise HTTPException(
                status_code=403, detail=f"User with ID: {user_id} is denied access to this cohort query"
            )

        # Validation is handled by FastAPI's path parameter validation
        # No need for explicit validation as in the original

        logger.debug("Gathering aggregated cohort results...")

        # Execute database query
        # Note we only expect one entry for a given query_id, so we can use first()
        db_query_stats = db.exec(select(QueryStats.stats).where(QueryStats.query_id == query_id)).first()

        logger.debug(f"query_id used: {query_id}")
        logger.debug(f"Database response: {db_query_stats}")

        if not db_query_stats:
            logger.error(f"No results found for query ID: {query_id}")
            raise HTTPException(status_code=404, detail="No results returned from the database")

        # Parse the results using model_validate instead of parse_obj
        query_stats = OmopCohortResultsResponse.model_validate(json.loads(db_query_stats))

        logger.info("Results have been successfully obtained")
        logger.debug(f"Query results: {query_stats}")

        # Return parsed results - FastAPI will handle JSON serialization
        return query_stats

    except HTTPException:
        # Re-raise HTTP exceptions so they maintain their status codes
        raise
    except Exception as e:
        logger.error(f"Error retrieving cohort query results: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
