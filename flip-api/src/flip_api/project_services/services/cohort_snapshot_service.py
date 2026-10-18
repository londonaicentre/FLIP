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

"""Per-trust state of a project's approval-time cohort freeze (FLIP#857).

A trust's state comes from its latest PERSIST_COHORT task for the project, joined to the hub's
``CohortSnapshotStatus`` row for the frozen facts. Shared by the snapshot listing, the re-freeze
endpoint and the backfill, so all three agree on which trusts are frozen, pending or failed.
"""

import json
from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

from sqlmodel import Session, col, select

from flip_api.db.models.main_models import CohortSnapshotStatus, Queries, Trust, TrustTask
from flip_api.domain.schemas.status import CohortSnapshotState, TaskStatus, TaskType
from flip_api.private_services.snapshot_notifications import MALFORMED_RESULT_ERROR

# Category-only failure text: the task result carries the trust's own error string, which is
# never passed through — it can name internals of the trust's stack.
NOT_REQUESTED = "No cohort snapshot was requested at this trust"
REFUSED = "Refused by the trust (for example, the cohort is below its disclosure threshold)"
REJECTED = "Rejected by the trust (the cohort query was not accepted)"
TIMED_OUT = "The trust did not report a result in time"
CANCELLED = "The snapshot task was cancelled"
TRUST_ERROR = "The trust could not freeze the cohort"
MALFORMED = "The trust's snapshot result could not be read"
# Prefixed to the category when a re-check of a frozen trust failed. The hub cannot tell a trust that still holds
# its membership (and keeps serving it) from one that lost it, so it says so rather than claiming either.
RECHECK_FAILED = "The last re-check failed ({reason}); this trust may no longer hold its frozen membership"


@dataclass(frozen=True)
class TrustSnapshotState:
    """One approved trust's cohort freeze state.

    Attributes:
        trust (Trust): The trust (id and name).
        state (CohortSnapshotState): Frozen, pending or failed.
        record (CohortSnapshotStatus | None): The hub's frozen-facts row, when the trust has reported one.
        error (str | None): Category-only reason: why the trust is FAILED, or, on a FROZEN trust, that its last
            re-check failed.
    """

    trust: Trust
    state: CohortSnapshotState
    record: CohortSnapshotStatus | None = None
    error: str | None = None


def failure_category(task: TrustTask) -> str:
    """Map a failed or cancelled PERSIST_COHORT task to a category-only reason.

    Args:
        task (TrustTask): The task.

    Returns:
        str: A fixed reason string; never the trust's raw error text.
    """
    if task.status == TaskStatus.CANCELLED:
        return CANCELLED
    try:
        result = json.loads(task.result) if task.result else {}
    except ValueError:
        result = {}
    if not isinstance(result, dict):
        result = {}
    status_code = result.get("status_code")
    if status_code == 403:
        return REFUSED
    if status_code in (400, 422):
        return REJECTED
    error = str(result.get("error", ""))
    if error.startswith("Exceeded maximum retries"):
        return TIMED_OUT
    if error == MALFORMED_RESULT_ERROR:
        return MALFORMED
    return TRUST_ERROR


def latest_persist_cohort_tasks(project_id: UUID, trust_ids: Iterable[UUID], db: Session) -> dict[UUID, TrustTask]:
    """The newest PERSIST_COHORT task per trust for a project.

    A PERSIST_COHORT task records the project's query of record in ``query_id``, so the tasks are
    found through the project's queries rather than by searching payloads.

    Args:
        project_id (UUID): The project.
        trust_ids (Iterable[UUID]): Trusts to look up.
        db (Session): Database session.

    Returns:
        dict[UUID, TrustTask]: Trust id to its newest task; trusts with none are absent.
    """
    ids = list(trust_ids)
    if not ids:
        return {}
    project_query_ids = select(Queries.id).where(Queries.project_id == project_id)
    tasks = db.exec(
        select(TrustTask)
        .where(TrustTask.task_type == TaskType.PERSIST_COHORT)
        .where(col(TrustTask.trust_id).in_(ids))
        .where(col(TrustTask.query_id).in_(project_query_ids))
        .order_by(col(TrustTask.created_at).desc())
    ).all()
    latest: dict[UUID, TrustTask] = {}
    for task in tasks:
        latest.setdefault(task.trust_id, task)
    return latest


def resolve_snapshot_states(project_id: UUID, trusts: list[Trust], db: Session) -> list[TrustSnapshotState]:
    """Derive each trust's cohort freeze state for a project.

    PENDING while the latest task is queued or running, or completed but its record not yet written
    (post-processing retries it); FAILED when it failed or was cancelled with no record, or when no task
    was ever queued and no record exists; FROZEN when a record exists and the latest task did not leave it
    pending. A failed latest task with a record is a failed re-check of a frozen trust: FROZEN, with
    ``error`` saying the re-check failed.

    Args:
        project_id (UUID): The project.
        trusts (list[Trust]): The trusts to resolve, in display order.
        db (Session): Database session.

    Returns:
        list[TrustSnapshotState]: One entry per trust, in the order given.
    """
    trust_ids = [trust.id for trust in trusts]
    tasks = latest_persist_cohort_tasks(project_id, trust_ids, db)
    records = (
        {
            record.trust_id: record
            for record in db.exec(
                select(CohortSnapshotStatus)
                .where(CohortSnapshotStatus.project_id == project_id)
                .where(col(CohortSnapshotStatus.trust_id).in_(trust_ids))
            ).all()
        }
        if trust_ids
        else {}
    )

    states = []
    for trust in trusts:
        task = tasks.get(trust.id)
        record = records.get(trust.id)
        if task is None:
            state = CohortSnapshotState.FROZEN if record else CohortSnapshotState.FAILED
            error = None if record else NOT_REQUESTED
        elif task.status in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS):
            state, error = CohortSnapshotState.PENDING, None
        elif task.status == TaskStatus.COMPLETED:
            state = CohortSnapshotState.FROZEN if record else CohortSnapshotState.PENDING
            error = None
        elif record:
            state, error = CohortSnapshotState.FROZEN, RECHECK_FAILED.format(reason=failure_category(task))
        else:
            state, error = CohortSnapshotState.FAILED, failure_category(task)
        states.append(TrustSnapshotState(trust=trust, state=state, record=record, error=error))
    return states
