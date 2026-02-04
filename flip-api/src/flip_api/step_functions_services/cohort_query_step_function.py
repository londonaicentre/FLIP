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

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlmodel import Session

from flip_api.auth.dependencies import verify_token
from flip_api.cohort_services.save_cohort_query import save_cohort_query
from flip_api.cohort_services.submit_cohort_query import submit_cohort_query
from flip_api.db.database import get_session
from flip_api.domain.schemas.cohort import (
    CohortQueryInput,
    SubmitCohortQuery,
    SubmitCohortQueryOutput,
)
from flip_api.utils.logger import logger
from flip_api.utils.project_manager import get_project_by_id

router = APIRouter(prefix="/step", tags=["step_functions_services"])


@router.post("/cohort", response_model=SubmitCohortQueryOutput, status_code=status.HTTP_201_CREATED)
def cohort_query_step_function_endpoint(
    request: Request,
    cohort_query: CohortQueryInput,
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
):
    """
    Process a cohort query by orchestrating the workflow:
    1. Validate the project exists.
    2. Save the cohort query.
    3. Submit the cohort query.

    This mimics the AWS Step Functions workflow defined in cohortQuery.yml

    Args:
        request (Request): The FastAPI request object.
        cohort_query (flip.domain.schemas.cohort.CohortQueryInput): The input data for the cohort query.
        db (Session): The database session.
        user_id (str): The ID of the current user.

    Returns:
        SubmitCohortQueryOutput: A dictionary containing the result of the cohort query submission.

    Raises:
        HTTPException: If an error occurs during any step of the process.
    """
    try:
        # Generate a request ID
        # request_id = str(uuid.uuid4())

        # Create request context
        # request_context = {"requestId": request_id}

        # Get Project (Check if project exists)
        if not get_project_by_id(project_id=cohort_query.project_id, db=db):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Project with ID {cohort_query.project_id} not found or deleted.",
            )

        # Save Cohort Query
        save_query_response = save_cohort_query(request=request, cohort_query=cohort_query, db=db)

        # Submit Cohort Query
        submit_query_input = SubmitCohortQuery(
            authenticationToken=request.headers.get("Authorization", ""),
            query=cohort_query.query,
            name=cohort_query.name,
            project_id=cohort_query.project_id,
            query_id=save_query_response.query_id,
        )

        submit_response = submit_cohort_query(request=request, cohort_query=submit_query_input, db=db)

        return submit_response

    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logger.exception(f"Unhandled error in cohort_query: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to process cohort query: {str(e)}"
        )
