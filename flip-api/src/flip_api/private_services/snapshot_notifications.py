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

"""Post-processing of completed PERSIST_COHORT tasks (FLIP#857).

Records the hub's audit row for the cohort membership a trust froze at approval — the answer to
"what cohort was this project approved for?" that #857 found missing — and surfaces
membership drift against the count the project was approved on. Aggregates only: the
row-level cohort never leaves the trust.
"""

import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import Session, select

from flip_api.db.models.main_models import (
    COHORT_SNAPSHOT_STATUS_UNIQUE,
    CohortSnapshotStatus,
    QueryStats,
    TrustTask,
    XNATProjectStatus,
)
from flip_api.domain.schemas.private import AggregatedCohortStats
from flip_api.domain.schemas.status import TaskStatus
from flip_api.utils.logger import logger

# Written as the task's result when a trust reports a snapshot the hub cannot read, so the trust shows as
# failed (and can be re-queued) rather than pending forever.
MALFORMED_RESULT_ERROR = "Malformed cohort snapshot result"


class TrustSnapshotResult(BaseModel):
    """What a trust's ``POST /cohort/snapshot`` reports — aggregates only. Every field the hub records is required:
    a missing or misspelt one is a malformed result, never a silent default."""

    model_config = ConfigDict(extra="ignore")

    row_count: int = Field(ge=0)
    has_accessions: StrictBool
    query_hash: str
    snapshot_at: datetime


def _approved_record_count(query_id: UUID | None, trust_id: UUID | None, db: Session) -> int | None:
    """The per-trust cohort count the project was staged/approved on, if recorded.

    Read from the aggregated statistics blob captured at submission time
    (``AggregatedCohortStats.trust_record_counts``). None when the stats row or the
    trust's entry is missing — old data or an errored trust — which downgrades the drift
    check to "not comparable", never blocks the audit row.
    """
    if query_id is None or trust_id is None:
        return None
    stats_row = db.exec(select(QueryStats).where(QueryStats.query_id == query_id)).first()
    if stats_row is None:
        return None
    try:
        stats = AggregatedCohortStats.model_validate(json.loads(stats_row.stats))
    except Exception:
        logger.warning(f"Could not parse QueryStats for query {query_id}; skipping drift comparison")
        return None
    return stats.trust_record_counts.get(str(trust_id))


def handle_snapshot_task_completed(task: TrustTask, db: Session) -> None:
    """Persist the frozen-cohort audit row for a successful PERSIST_COHORT task.

    Upserts the one ``CohortSnapshotStatus`` row per (project, trust) with a single
    ``INSERT ... ON CONFLICT DO UPDATE`` on the table's unique constraint, so a re-queued
    snapshot (or two post-processing runs racing — submission and the recovery job) updates the row
    in place rather than duplicating it or failing on the constraint. The row holds the
    approval-time facts; the frozen membership bounds what the project trains on at that
    trust (it can shrink, never grow). Logs a WARNING when the frozen member count differs
    from the count the project was approved on: the live cohort drifted between submission
    and approval. The drift is surfaced, never acted on.

    A result that does not validate as ``TrustSnapshotResult`` can never be recorded, so retrying it would
    leave the trust pending forever: the task is marked FAILED instead, which the snapshot listing shows and
    the re-queue route accepts.

    A trust's FIRST record for an imaging project whose XNAT project already exists means the freeze landed
    after imaging creation — which found no frozen accession set and imported nothing. The project's reimport
    budget at that trust is reset so the reimport sweep pulls the now-frozen studies, however many sweeps the
    refused attempts used up.

    Called after the task result has been committed to the database.
    Any exceptions are expected to be caught by the caller.

    Args:
        task (TrustTask): The completed PERSIST_COHORT task with result data.
        db (Session): Database session.

    """
    try:
        snapshot = TrustSnapshotResult.model_validate_json(task.result or "")
    except ValidationError as e:
        # Field names and error types only: the input values are the trust's raw result.
        problems = sorted({f"{'.'.join(map(str, err['loc'])) or 'result'}: {err['type']}" for err in e.errors()})
        logger.error(
            f"PERSIST_COHORT task {task.id} reported a malformed snapshot result {problems}; marking it failed"
        )
        task.status = TaskStatus.FAILED
        task.result = json.dumps({"error": MALFORMED_RESULT_ERROR})
        db.commit()
        return

    payload = json.loads(task.payload)
    project_id = UUID(payload["project_id"])
    query_id = UUID(payload["query_id"]) if payload.get("query_id") else None

    row_count = snapshot.row_count
    approved_count = _approved_record_count(query_id, task.trust_id, db)
    if approved_count is not None and approved_count != row_count:
        logger.warning(
            f"Cohort membership drift for project {project_id}, trust {task.trust_id}: approved on "
            f"{approved_count} records, frozen membership holds {row_count}. The live cohort changed "
            "between submission and approval; the frozen membership bounds what the project trains on."
        )

    facts = {
        "query_id": query_id,
        "row_count": row_count,
        "approved_record_count": approved_count,
        "has_accessions": snapshot.has_accessions,
        "query_hash": snapshot.query_hash,
        "snapshot_at": snapshot.snapshot_at,
    }
    first_record = (
        db.exec(
            select(CohortSnapshotStatus.id)
            .where(CohortSnapshotStatus.project_id == project_id)
            .where(CohortSnapshotStatus.trust_id == task.trust_id)
        ).first()
        is None
    )
    statement = (
        pg_insert(CohortSnapshotStatus)
        .values(id=uuid4(), project_id=project_id, trust_id=task.trust_id, created_at=datetime.now(UTC), **facts)
        .on_conflict_do_update(constraint=COHORT_SNAPSHOT_STATUS_UNIQUE, set_=facts)
    )
    db.execute(statement)
    if first_record and snapshot.has_accessions:
        _reopen_reimport_after_late_freeze(project_id, task.trust_id, db)
    db.commit()
    logger.info(
        f"Recorded cohort snapshot for project {project_id}, trust {task.trust_id}: "
        f"{row_count} members, has_accessions={facts['has_accessions']}"
    )


def _reopen_reimport_after_late_freeze(project_id: UUID, trust_id: UUID | None, db: Session) -> None:
    """Reset the reimport budget of an imaging project created before its cohort was frozen at this trust.

    The sweep spends one of ``MAX_REIMPORT_COUNT`` attempts per run whether or not the trust released any
    accession ids, so a freeze that lands late would otherwise find the budget gone and the cohort's studies
    never pulled. A no-op at an ordinary approval, where the freeze runs before imaging creation.
    """
    status_row = db.exec(
        select(XNATProjectStatus)
        .where(XNATProjectStatus.project_id == project_id)
        .where(XNATProjectStatus.trust_id == trust_id)
    ).first()
    if status_row is None:
        return
    logger.info(
        f"Cohort for project {project_id} froze at trust {trust_id} after its imaging project was created; "
        f"reset its reimport budget (was {status_row.reimport_count}) so the sweep pulls the frozen studies"
    )
    status_row.reimport_count = 0
