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

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from flip_api.auth.access_manager import can_access_project
from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.domain.interfaces.project import ICohortSnapshot
from flip_api.project_services.services.cohort_snapshot_service import resolve_snapshot_states
from flip_api.project_services.services.project_services import get_approved_trusts_for_project
from flip_api.utils.logger import logger

router = APIRouter(prefix="/projects", tags=["project_services"])


@router.get(
    "/{project_id}/cohort-snapshots",
    summary="Get each approved trust's approval-time cohort freeze for a project.",
    response_model=list[ICohortSnapshot],
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_200_OK: {
            "model": list[ICohortSnapshot],
            "description": "One entry per approved trust: frozen, pending or failed (empty until a trust approves).",
        },
        status.HTTP_403_FORBIDDEN: {
            "model": None,
            "description": "You do not have permission to access this project.",
        },
    },
)
async def get_cohort_snapshots(
    project_id: UUID,
    session: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
) -> list[ICohortSnapshot]:
    """
    Get each approved trust's record of the cohort membership frozen at project approval (FLIP#857).

    Aggregates only — the row-level cohort never leaves each trust. One entry per approved trust, with
    a ``status`` from its latest PERSIST_COHORT task: ``frozen`` (with the approval-time facts),
    ``pending`` (queued, running, or its record not yet written) or ``failed`` (with a category-only
    ``error``; also used when no snapshot was ever requested, e.g. a project approved before the
    feature). Training at a trust never frozen is refused there; a ``frozen`` trust with an ``error`` is one
    whose last re-check failed. A ``rowCount`` differing from
    ``approvedRecordCount`` means the live cohort drifted between submission and approval — surfaced
    here so the drift is visible, never silently adopted.

    Args:
        project_id (UUID): The ID of the project.
        session (Session): The database session.
        user_id (UUID): The ID of the user.

    Returns:
        list[ICohortSnapshot]: One entry per approved trust.

    Raises:
        HTTPException: If the user does not have permission to access the project.
    """
    logger.info(f"Getting cohort snapshots for project {project_id}")

    if not can_access_project(user_id, project_id, session):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to access this project.",
        )

    trusts = get_approved_trusts_for_project(project_id, session)
    snapshots = []
    for entry in resolve_snapshot_states(project_id, trusts, session):
        record = entry.record
        snapshots.append(
            ICohortSnapshot(  # type: ignore[call-arg]  # populate_by_name: field names are valid at runtime
                trust_id=entry.trust.id,
                trust_name=entry.trust.name,
                status=entry.state,
                error=entry.error,
                row_count=record.row_count if record else None,
                approved_record_count=record.approved_record_count if record else None,
                has_accessions=record.has_accessions if record else None,
                snapshot_at=record.snapshot_at if record else None,
                query_id=record.query_id if record else None,
            )
        )
    return snapshots
