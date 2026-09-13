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
from sqlmodel import Session, select

from flip_api.auth.access_manager import can_access_project
from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.db.models.main_models import CohortSnapshotStatus, Trust
from flip_api.domain.interfaces.project import ICohortSnapshot
from flip_api.utils.logger import logger

router = APIRouter(prefix="/projects", tags=["project_services"])


@router.get(
    "/{project_id}/cohort-snapshots",
    summary="Get the per-trust frozen approved-cohort records for a project.",
    response_model=list[ICohortSnapshot],
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_200_OK: {
            "model": list[ICohortSnapshot],
            "description": "The per-trust cohort snapshot records (empty until trusts report snapshots).",
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
    Get the per-trust record of the cohort frozen at project approval (FLIP#857).

    Aggregates only — the row-level cohort never leaves each trust. One entry per trust
    that has completed its PERSIST_COHORT task; a trust missing from the list has not
    reported a snapshot (task pending/failed, or the project predates the feature), and
    its row-level routes will refuse serving until it does. A ``rowCount`` differing from
    ``approvedRecordCount`` means the live cohort drifted between submission and approval —
    surfaced here so the drift is visible, never silently adopted.

    Args:
        project_id (UUID): The ID of the project.
        session (Session): The database session.
        user_id (UUID): The ID of the user.

    Returns:
        list[ICohortSnapshot]: One frozen-cohort record per reporting trust.

    Raises:
        HTTPException: If the user does not have permission to access the project.
    """
    logger.info(f"Getting cohort snapshots for project {project_id}")

    if not can_access_project(user_id, project_id, session):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to access this project.",
        )

    rows = session.exec(
        select(CohortSnapshotStatus, Trust)
        .join(Trust, Trust.id == CohortSnapshotStatus.trust_id)  # type: ignore[arg-type]
        .where(CohortSnapshotStatus.project_id == project_id)
    ).all()

    return [
        ICohortSnapshot(  # type: ignore[call-arg]  # populate_by_name: field names are valid at runtime
            trust_id=snapshot.trust_id,
            trust_name=trust.name,
            row_count=snapshot.row_count,
            approved_record_count=snapshot.approved_record_count,
            has_accessions=snapshot.has_accessions,
            snapshot_at=snapshot.snapshot_at,
            query_id=snapshot.query_id,
        )
        for snapshot, trust in rows
    ]
