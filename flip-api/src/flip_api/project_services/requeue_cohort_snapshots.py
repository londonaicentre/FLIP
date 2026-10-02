# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
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

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlmodel import Session

from flip_api.auth.dependencies import verify_token
from flip_api.auth.trust_authority import decision_maker_for, trusts_with_admin
from flip_api.db.database import get_session
from flip_api.db.models.main_models import Trust
from flip_api.domain.interfaces.project import ICohortSnapshotRequeue
from flip_api.domain.interfaces.trust import ITrust
from flip_api.domain.schemas.status import CohortSnapshotState, ProjectStatus
from flip_api.project_services.services.cohort_snapshot_service import TrustSnapshotState, resolve_snapshot_states
from flip_api.project_services.services.project_services import get_approved_trusts_for_project, get_project
from flip_api.trusts_services.start_project_imaging_creation import queue_cohort_snapshot
from flip_api.utils.logger import logger

router = APIRouter(prefix="/projects", tags=["project_services"])

ALREADY_FROZEN = "Already frozen; re-check with include_frozen if the trust may have lost its frozen membership"
ALREADY_PENDING = "A cohort snapshot is already pending at this trust"


def requeue_trust_snapshots(
    project_id: UUID, trusts: list[Trust], db: Session, include_frozen: bool = False
) -> list[ICohortSnapshotRequeue]:
    """Queue PERSIST_COHORT at each of ``trusts`` not pending — and, unless ``include_frozen``, not frozen.

    No authority check: the caller has already narrowed ``trusts``.

    A pending trust already has a task queued. A frozen trust is skipped unless ``include_frozen``: the hub's record
    says it holds a membership, but cannot see whether the trust still does (a lost volume, an unreadable record).
    Re-queuing one is safe because a trust never replaces a membership it holds — it answers with the frozen
    record's facts — so only a trust that has lost its record freezes afresh, from live OMOP.

    Args:
        project_id (UUID): The approved project.
        trusts (list[Trust]): Approved trusts to consider.
        db (Session): Database session.
        include_frozen (bool): Also re-queue trusts the hub records as frozen.

    Returns:
        list[ICohortSnapshotRequeue]: What was done at each trust, in the order given.
    """
    return [
        _requeue_one(project_id, entry, db, include_frozen) for entry in resolve_snapshot_states(project_id, trusts, db)
    ]


def _requeue_one(
    project_id: UUID, entry: TrustSnapshotState, db: Session, include_frozen: bool
) -> ICohortSnapshotRequeue:
    trust = entry.trust

    def outcome(queued: bool, state: CohortSnapshotState, reason: str | None = None) -> ICohortSnapshotRequeue:
        return ICohortSnapshotRequeue(  # type: ignore[call-arg]  # populate_by_name: field names are valid at runtime
            trust_id=trust.id, trust_name=trust.name, queued=queued, status=state, reason=reason
        )

    if entry.state == CohortSnapshotState.FROZEN and not include_frozen:
        return outcome(False, entry.state, ALREADY_FROZEN)
    if entry.state == CohortSnapshotState.PENDING:
        return outcome(False, entry.state, ALREADY_PENDING)
    try:
        queue_cohort_snapshot(project_id=project_id, trust=ITrust(id=trust.id, name=trust.name), db=db)
    except HTTPException as e:
        logger.error(f"Could not re-queue the cohort snapshot for project {project_id} at trust {trust.name}: {e}")
        return outcome(False, entry.state, str(e.detail))
    logger.info(f"Re-queued the cohort snapshot for project {project_id} at trust {trust.name}")
    return outcome(True, CohortSnapshotState.PENDING)


@router.post(
    "/{project_id}/cohort-snapshots",
    summary="Re-queue the approval-time cohort freeze at every approved trust where it is missing or failed.",
    response_model=list[ICohortSnapshotRequeue],
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_403_FORBIDDEN: {"model": None, "description": "You may not decide this project at any trust."},
        status.HTTP_404_NOT_FOUND: {"model": None, "description": "The project does not exist."},
        status.HTTP_409_CONFLICT: {"model": None, "description": "The project is not approved."},
    },
)
def requeue_cohort_snapshots(
    project_id: UUID = Path(..., description="The approved project whose cohort snapshots to re-queue."),
    include_frozen: bool = Query(
        False,
        description=(
            "Also re-queue trusts recorded as frozen, to recover one that lost its frozen membership. A trust that "
            "still holds its membership keeps it unchanged."
        ),
    ),
    user_id: UUID = Depends(verify_token),
    db: Session = Depends(get_session),
) -> list[ICohortSnapshotRequeue]:
    """
    Re-queues the PERSIST_COHORT task (FLIP#857) at approved trusts whose cohort is not frozen.

    For a project approved before cohort snapshots existed, or one whose snapshot failed at a trust: the trust
    refuses to serve the project's cohort until it holds a frozen membership. Covers only the approved trusts the
    caller may decide — the same per-trust authority that approves the project (a trust's Trust Admin where it has
    one, the hub admin where it does not). Pending trusts are reported, not re-queued; so are frozen ones unless
    ``include_frozen`` is set — the recovery for a trust whose store was lost, which the hub cannot see.

    Args:
        project_id (UUID): The approved project.
        include_frozen (bool): Also re-queue trusts recorded as frozen.
        user_id (UUID): The caller.
        db (Session): Database session.

    Returns:
        list[ICohortSnapshotRequeue]: One entry per approved trust the caller may decide.

    Raises:
        HTTPException: 404 if the project does not exist; 409 if it is not approved; 403 if the caller may decide
                       none of its approved trusts.
    """
    project = get_project(project_id, db)
    if project.status != ProjectStatus.APPROVED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Project {project_id} is not approved; there is no approved cohort to freeze.",
        )

    trusts = get_approved_trusts_for_project(project_id, db)
    site_run = trusts_with_admin([trust.id for trust in trusts], db)
    decidable = [trust for trust in trusts if decision_maker_for(user_id, trust.id, db, has_admin=trust.id in site_run)]
    if not decidable:
        logger.error(f"User {user_id} may not re-queue cohort snapshots for project {project_id} at any trust")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"User with ID: {user_id} was unable to re-queue this project's cohort snapshots",
        )

    return requeue_trust_snapshots(project_id, decidable, db, include_frozen=include_frozen)
