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
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session

from flip_api.auth.dependencies import verify_token
from flip_api.auth.identity import IdentityProvider, get_identity_provider
from flip_api.db.database import get_session
from flip_api.domain.interfaces.trust import ITrust
from flip_api.domain.schemas.projects import ApproveProjectBodyPayload
from flip_api.domain.schemas.status import ProjectStatus
from flip_api.project_services.approve_project import approve_project_endpoint
from flip_api.trusts_services.start_project_imaging_creation import queue_imaging_creation
from flip_api.utils.logger import logger
from flip_api.utils.project_manager import get_project_by_id

router = APIRouter(prefix="/step", tags=["step_functions_services"])


async def process_trust(
    request: Request, project_id: UUID, trust: ITrust, db: Session, idp: IdentityProvider
) -> dict[str, Any]:
    """
    Process a single trust by starting the imaging project creation.

    Args:
        request (Request): The FastAPI request object.
        project_id (UUID): The ID of the project.
        trust (ITrust): The trust to process (one element of the list returned by
            ``approve_project_endpoint``).
        db (Session): The database session.
        idp (IdentityProvider): The identity provider, resolved once by the endpoint and handed to
            ``queue_imaging_creation``, a plain function with no ``Depends()`` of its own.

    Returns:
        dict[str, Any]: A dictionary containing the result of the imaging creation for the trust.
    """
    try:
        # Start creating an imaging project for this trust. Not through the route's per-trust authority
        # check: the trusts include any approved by an earlier call, at which this caller may hold none.
        await queue_imaging_creation(request=request, project_id=project_id, trust=trust, db=db, idp=idp)

        return {"trust": trust.name, "success": True, "message": "Imaging started successfully"}

    except Exception as e:
        logger.exception(f"Error processing trust {trust.name}: {str(e)}")
        return {"trust": trust.name, "success": False, "message": str(e)}


@router.post("/project/{project_id}/approve", response_model=dict[str, Any])
async def approve_project_step_function_endpoint(
    project_id: UUID,
    body: ApproveProjectBodyPayload,
    request: Request,
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
    idp: IdentityProvider = Depends(get_identity_provider),
) -> dict[str, Any]:
    """
    Records trust decisions on a project and starts image creation on every trust they activate — on the call that
    approves the project every trust approved so far, on a later call the trusts it newly approved (FLIP#1258) —
    unless the project was created without imaging (``has_imaging=False``, FLIP#1071), in which case the imaging
    stage is skipped. Decisions that activate no trust dispatch nothing.

    This mimics the AWS Step Functions workflow defined in approveProject.yml

    Args:
        project_id (UUID): The ID of the project to approve.
        body (ApproveProjectBodyPayload): The trust IDs that approve the project and those that decline it.
        request (Request): The FastAPI request object.
        db (Session): The database session.
        user_id (UUID): The ID of the current user.
        idp (IdentityProvider): The identity provider, handed to the per-trust imaging fan-out.

    Returns:
        dict[str, Any]: The project's status after the decisions and the result of the imaging creation process.

    Raises:
        HTTPException: If an error occurs during the approval or imaging creation process.
    """
    try:
        logger.debug(
            f"Recording trust decisions for project {project_id}: approve {body.trusts}, decline {body.declined}"
        )

        # FLIP#1071: read the project's kind before approval commits, so the fan-out decision below
        # needs no post-commit round-trip (a DB blip there would report "failed to approve" on a
        # project that IS approved). Deliberately NOT a 404 here: this route only authenticates
        # (verify_token) — CAN_APPROVE_FOR_TRUST is checked per trust inside
        # approve_project_endpoint — so
        # refusing a missing row first would tell a caller without that permission whether a project
        # exists (404) or not (403). A missing row falls through to approve_project_endpoint, which
        # checks the permission before it 404s, and never reaches the fan-out either way.
        project = get_project_by_id(project_id, db)
        has_imaging = project.has_imaging if project is not None else True

        # Step 1: Record the trust decisions (approves the project once every trust is decided and one approved)
        logger.info(f"Recording trust decisions on project {project_id}")
        trusts = approve_project_endpoint(project_id=project_id, payload=body, user_id=user_id, db=db)
        logger.debug(f"Trusts returned from approve_project: {trusts}")

        # Step 2: No trusts back means these decisions start nothing — nothing approved yet, a late decline, or an
        # approval re-sent. The project may be STAGED or already APPROVED, so its status is read back.
        if not trusts:
            logger.info(f"Trust decisions on project {project_id} start no trust")
            after = get_project_by_id(project_id, db)
            return {
                "message": "Trust decisions recorded; nothing to start",
                "projectId": project_id,
                "projectStatus": after.status if after is not None else ProjectStatus.STAGED,
            }

        # Step 3: For Each Trust — unless the project has no imaging. FLIP#1071: this is the only
        # place the hub decides what it dispatches for a tabular-only project. The project is
        # approved above exactly as before; what changes is that no CREATE_IMAGING task is queued, so
        # no trust creates an XNAT project or calls the accession-ids route — the flag is never sent
        # to a trust. An empty result set then reports zero trusts processed through the one response
        # contract below.
        if has_imaging:
            logger.info(f"Processing {len(trusts)} trusts for project {project_id}")
            # Execute trust processing in parallel
            trust_tasks = [process_trust(request, project_id, trust, db, idp) for trust in trusts]
            start_image_results = await asyncio.gather(*trust_tasks)
            message = "Project approval workflow completed"
        else:
            logger.info(f"Project {project_id} has no imaging: skipping the imaging fan-out to {len(trusts)} trust(s)")
            start_image_results = []
            message = "Project approved; imaging stage skipped (project has no imaging)"

        # Check if any trust processing failed
        failures = [result for result in start_image_results if not result.get("success")]
        processed = len(start_image_results)

        # Return final response
        return {
            "message": message,
            "projectId": project_id,
            "projectStatus": ProjectStatus.APPROVED,
            "successful": len(failures) == 0,
            "trusts": {"processed": processed, "succeeded": processed - len(failures), "failed": len(failures)},
            "details": start_image_results,
        }

    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logger.exception("Unhandled error in approve_project")
        raise HTTPException(status_code=500, detail="Internal server error") from e
