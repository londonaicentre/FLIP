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
from flip_api.db.database import get_session
from flip_api.db.models.main_models import Queries
from flip_api.domain.schemas.cohort import CohortQueryInput, SubmitCohortQueryInput
from flip_api.domain.schemas.status import ProjectStatus
from flip_api.utils.logger import logger
from flip_api.utils.project_manager import has_project_status

router = APIRouter(prefix="/cohort", tags=["cohort_services"])


# TODO [#114] This endpoint was not defined in the old repo. The old repo defined a step function that ran the
# following steps: (1) getProject, (2) save_cohort_query, (3) submit_cohort_query
@router.post(
    "/save",
    response_model=SubmitCohortQueryInput,
    status_code=status.HTTP_201_CREATED,
    summary="Save a new cohort query",
    description="Saves a new cohort query to the database.",
)
def save_cohort_query(
    request: Request,
    cohort_query: CohortQueryInput,
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
) -> SubmitCohortQueryInput:
    """
    Save a new cohort query to the database.

    Args:
        request: HTTP request object
        cohort_query: CohortQueryInput object containing the query details
        db: Database session
        user_id: ID of the user making the request, obtained from authentication

    Returns:
        SubmitCohortQueryInput: The saved cohort query details including the query ID
    """
    try:
        # Validation is handled automatically by Pydantic

        # Validate whether project has UNSTAGED status
        if not has_project_status(cohort_query.project_id, ProjectStatus.UNSTAGED, db):
            raise HTTPException(
                status_code=400, detail="Unable to run the cohort query as the project has been staged/approved"
            )

        # Create new query
        new_query = Queries(name=cohort_query.name, query=cohort_query.query, project_id=cohort_query.project_id)

        # Add and commit to get the ID
        db.add(new_query)
        db.commit()
        db.refresh(new_query)

        if not new_query.id:
            raise HTTPException(status_code=400, detail="Could not create query")

        # Prepare output
        output = SubmitCohortQueryInput(
            query=cohort_query.query,
            name=cohort_query.name,
            project_id=cohort_query.project_id,
            query_id=new_query.id,
            authenticationToken=request.headers.get("Authorization", ""),
        )  # type: ignore[call-arg]

        logger.info(f"Successfully created cohort query with ID: {new_query.id}")

        return output

    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logger.error(f"Error saving cohort query: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
