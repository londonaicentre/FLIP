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
from fastapi.responses import JSONResponse
from sqlmodel import Session, select

from flip_api.auth.access_manager import can_access_cohort_query
from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.db.models.main_models import Queries, QueryStats
from flip_api.domain.schemas.cohort import OmopCohortResultsResponse
from flip_api.utils.logger import logger

router = APIRouter(prefix="/cohort", tags=["cohort_services"])


# [#114] ✅
@router.get(
    "/{query_id}",
    response_model=OmopCohortResultsResponse,
    summary="Get cohort query results",
    description=(
        "Retrieve the aggregated results of a cohort query. Returns 202 while the query is "
        "still pending trust responses, 200 once results are available, and 404 only when the "
        "query id is unknown."
    ),
    responses={
        202: {"description": "Query exists but no results have been posted by trusts yet."},
        404: {"description": "No cohort query exists with the supplied id."},
    },
)
def get_cohort_query_results(
    query_id: UUID = Path(..., description="Unique identifier of the cohort query"),
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
) -> OmopCohortResultsResponse | JSONResponse:
    """
    Retrieve the aggregated results of a cohort query.

    Cohort queries are async: the hub queues tasks for all trusts, each trust runs the query
    against its OMOP database and posts results back. Until the first trust responds, the
    query exists but has no stats. This endpoint distinguishes three states:

    * **200** — stats are populated, return them.
    * **202** — query is known but still pending trust responses.
    * **404** — query id is unknown (or access-layer denies it).

    Args:
        query_id (UUID): Unique identifier of the cohort query.
        db (Session): Database session dependency.
        user_id (UUID): ID of the user making the request, obtained from authentication.

    Returns:
        OmopCohortResultsResponse | JSONResponse: Results (200) or a pending marker (202).
    """
    try:
        logger.info(f"Checking access for user: {user_id} to cohort query: {query_id}")

        if not can_access_cohort_query(user_id, query_id, db):
            logger.info(f"Access denied: User {user_id} attempted to access cohort query {query_id}")
            raise HTTPException(
                status_code=403, detail=f"User with ID: {user_id} is denied access to this cohort query"
            )

        # Step 1: does the query exist at all? Needed so we can distinguish "unknown id"
        # (404) from "known but pending" (202). Without this, a freshly-submitted query
        # spuriously returns 404 during the window between POST /step/cohort and the first
        # trust's POST /cohort/results — which surfaces as a noisy error in the browser
        # console while the UI polls.
        query_exists = db.exec(select(Queries.id).where(Queries.id == query_id)).first()
        if not query_exists:
            logger.info(f"Cohort query not found for id: {query_id}")
            raise HTTPException(status_code=404, detail=f"Cohort query not found for id: {query_id}")

        # Step 2: results lookup. A single QueryStats row is expected per query.
        db_query_stats = db.exec(select(QueryStats.stats).where(QueryStats.query_id == query_id)).first()

        if not db_query_stats:
            logger.debug(f"Cohort query {query_id} is still pending trust responses")
            return JSONResponse(
                status_code=202,
                content={"status": "pending", "detail": "Results not yet available — trust responses pending"},
            )

        query_stats = OmopCohortResultsResponse.model_validate(json.loads(db_query_stats))
        logger.info("Results have been successfully obtained")
        return query_stats

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving cohort query results: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
