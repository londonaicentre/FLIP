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

Records the hub's audit row for the cohort a trust froze at approval — the answer to
"what cohort was this project approved for?" that #857 found missing — and surfaces
membership drift against the count the project was approved on. Aggregates only: the
row-level cohort never leaves the trust.
"""

import json
from datetime import datetime
from uuid import UUID

from sqlmodel import Session, select

from flip_api.db.models.main_models import CohortSnapshotStatus, QueryStats, TrustTask
from flip_api.domain.schemas.private import AggregatedCohortStats
from flip_api.utils.logger import logger


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

    Upserts one ``CohortSnapshotStatus`` row per (project, trust) — re-approval replaces
    the trust-side artefact, so the newest snapshot's facts overwrite the row. Logs a
    WARNING when the frozen row count differs from the count the project was approved on:
    the live cohort drifted between submission and approval. The drift is surfaced, never
    acted on — the snapshot IS the approved cohort from here on.

    Called after the task result has been committed to the database.
    Any exceptions are expected to be caught by the caller.

    Args:
        task (TrustTask): The completed PERSIST_COHORT task with result data.
        db (Session): Database session.

    Raises:
        ValueError: If the task has no result data.
    """
    if not task.result:
        raise ValueError(f"Task {task.id} has no result data")
    snapshot = json.loads(task.result)

    payload = json.loads(task.payload)
    project_id = UUID(payload["project_id"])
    query_id = UUID(payload["query_id"]) if payload.get("query_id") else None

    row_count = int(snapshot["row_count"])
    approved_count = _approved_record_count(query_id, task.trust_id, db)
    if approved_count is not None and approved_count != row_count:
        logger.warning(
            f"Cohort membership drift for project {project_id}, trust {task.trust_id}: approved on "
            f"{approved_count} records, frozen snapshot holds {row_count}. The live cohort changed "
            "between submission and approval; the snapshot is what the project will train on."
        )

    existing = db.exec(
        select(CohortSnapshotStatus)
        .where(CohortSnapshotStatus.project_id == project_id)
        .where(CohortSnapshotStatus.trust_id == task.trust_id)
    ).first()
    status_row = existing or CohortSnapshotStatus(project_id=project_id, trust_id=task.trust_id, row_count=row_count)
    status_row.query_id = query_id
    status_row.row_count = row_count
    status_row.approved_record_count = approved_count
    status_row.has_accessions = bool(snapshot.get("has_accessions", False))
    status_row.query_hash = snapshot.get("query_hash")
    status_row.snapshot_at = datetime.fromisoformat(snapshot["snapshot_at"])
    db.add(status_row)
    db.commit()
    logger.info(
        f"Recorded cohort snapshot for project {project_id}, trust {task.trust_id}: "
        f"{row_count} rows, has_accessions={status_row.has_accessions}"
    )
