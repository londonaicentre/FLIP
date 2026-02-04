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

import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session

from flip_api.auth.dependencies import verify_token
from flip.db.database import get_session
from flip.domain.schemas.private import ProjectApprovalBody
from flip.domain.schemas.projects import ApproveProjectBodyPayload
from flip.project_services.approve_project import approve_project_endpoint
from flip.trusts_services.start_project_imaging_creation import start_project_imaging_creation
from flip.utils.logger import logger

router = APIRouter(prefix="/step", tags=["step_functions_services"])


async def process_trust(request, project_id, trust, db, user_id):
    """
    Process a single trust by starting the imaging project creation.

    Args:
        request (Request): The FastAPI request object.
        project_id (UUID): The ID of the project.
        trust: The trust object to process.
        db (Session): The database session.
        user_id (UUID): The ID of the current user.

    Returns:
        dict: A dictionary containing the result of the imaging creation for the trust.

    Raises:
        Exception: If an error occurs during the imaging creation process.
    """
    try:
        # Start creating an imaging project for this trust
        await start_project_imaging_creation(
            request=request, project_id=project_id, trust=trust, db=db, user_id=user_id
        )

        return {"trust": trust.name, "success": True, "message": "Imaging started successfully"}

    except Exception as e:
        logger.exception(f"Error processing trust {trust.name}: {str(e)}")
        return {"trust": trust.name, "success": False, "message": str(e)}


@router.post("/project/{project_id}/approve")
async def approve_project_step_function_endpoint(
    project_id: UUID,
    body: ProjectApprovalBody,
    request: Request,
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
):
    """
    Approves a project and starts image creation on all connected trusts

    This mimics the AWS Step Functions workflow defined in approveProject.yml

    Args:
        project_id (UUID): The ID of the project to approve.
        body (ProjectApprovalBody): The body containing trust IDs to process.
        request (Request): The FastAPI request object.
        db (Session): The database session.
        user_id (UUID): The ID of the current user.

    Returns:
        dict: A dictionary containing the result of the approval and imaging creation process.

    Raises:
        HTTPException: If an error occurs during the approval or imaging creation process.
    """
    try:
        trust_ids = body.trusts

        # Prepare the payload for approve_project
        payload = ApproveProjectBodyPayload(
            trusts=trust_ids,
        )
        logger.debug(f"Starting approval for project {project_id} with trusts: {trust_ids}")

        # Step 1: Approve Project
        logger.info(f"Approving project with ID: {project_id}")
        trusts = approve_project_endpoint(project_id=project_id, payload=payload, user_id=user_id, db=db)
        print(f"Trusts returned from approve_project: {trusts}")

        # Step 2: Format Trusts
        if not trusts:
            logger.warning("No trusts found for project")
            return {"message": "Project approved but no trusts to process"}

        logger.info(f"Processing {len(trusts)} trusts for project {project_id}")

        # Step 3: For Each Trust
        start_image_results = []

        # Execute trust processing in parallel
        trust_tasks = [process_trust(request, project_id, trust, db, user_id) for trust in trusts]
        start_image_results = await asyncio.gather(*trust_tasks)

        # Check if any trust processing failed
        failures = [result for result in start_image_results if not result.get("success")]

        # Return final response
        return {
            "message": "Project approval workflow completed",
            "projectId": project_id,
            "successful": len(failures) == 0,
            "trusts": {"processed": len(trusts), "succeeded": len(trusts) - len(failures), "failed": len(failures)},
            "details": start_image_results,
        }

    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logger.exception(f"Unhandled error in approve_project: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to approve project: {str(e)}")
