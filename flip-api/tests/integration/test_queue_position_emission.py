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

"""Integration coverage of the queue-position emission (issue #788).

``log_queue_positions`` is SQL-shaped end to end: the ranks come from the
``created``-ascending (``id``-tiebroken) queue query, the emit-on-change dedup
collapses each job's history to its newest row inside Postgres (``DISTINCT ON``
over the JSONB job id, ``log_date``-descending), the compared payload
round-trips through the JSONB ``details`` column, and the batch relies on
``add_log`` deferring its per-row commit under ``transaction=``. All of that
passes silently under a mocked session — the unit tests fabricate both result
sets — so the invariants are exercised here against the throwaway Postgres,
ending with the rendered feed a researcher actually sees.
"""

from datetime import datetime
from uuid import UUID

from fastapi.testclient import TestClient
from sqlmodel import col, select

from flip_api.db.models.main_models import FLJob, FLLogs
from flip_api.domain.schemas.status import JobStatus, ModelStatus
from flip_api.domain.schemas.types import FLLogEvent
from flip_api.fl_services.services.fl_scheduler_service import log_queue_positions
from tests.integration.conftest import admin_user, override_verify_token_as
from tests.integration.test_all_models_endpoint import _add_model, _add_project


def _positions_by_job(session) -> dict[str, list[int]]:
    """All persisted QUEUE_POSITION rows oldest-first, keyed by the job id in ``details``."""
    rows = session.exec(
        select(FLLogs)
        .where(FLLogs.event_type == FLLogEvent.QUEUE_POSITION.value)
        .order_by(col(FLLogs.log_date).asc())
    ).all()
    by_job: dict[str, list[int]] = {}
    for row in rows:
        details = row.details or {}
        by_job.setdefault(details["job_id"], []).append(details["position"])
    return by_job


def test_queue_position_emission_ranks_dedups_and_renders(
    client: TestClient, session, project_factory, model_factory
):
    """One pass over the write path: rank → idempotence → re-rank → served feed."""
    admin_id = admin_user(session)
    project = _add_project(session, project_factory, owner_id=admin_id, name="Queue emission")
    head = _add_model(
        session, model_factory, project_id=project.id, owner_id=admin_id,
        name="head", status=ModelStatus.INITIATED,
    )
    mid = _add_model(
        session, model_factory, project_id=project.id, owner_id=admin_id,
        name="mid", status=ModelStatus.INITIATED,
    )
    tail = _add_model(
        session, model_factory, project_id=project.id, owner_id=admin_id,
        name="tail", status=ModelStatus.INITIATED,
    )

    head_job = FLJob(model_id=head.id, created=datetime(2026, 1, 1, 10, 0, 0))
    # mid and tail share a created timestamp: their relative ranks must come
    # from the id tiebreak, the same secondary key check_for_queued_jobs
    # picks by, so the emitted positions cannot disagree with pickup order.
    mid_job = FLJob(
        id=UUID("00000000-0000-4000-8000-000000000001"),
        model_id=mid.id,
        created=datetime(2026, 1, 1, 10, 5, 0),
    )
    tail_job = FLJob(
        id=UUID("ffffffff-ffff-4fff-bfff-fffffffffffe"),
        model_id=tail.id,
        created=datetime(2026, 1, 1, 10, 5, 0),
    )
    session.add(head_job)
    session.add(mid_job)
    session.add(tail_job)
    session.commit()

    # First emission ranks the whole queue (created asc, id tiebreak) and the
    # {"position", "job_id"} payload survives the JSONB round-trip. The rows
    # are readable through the API's own session, so the batch committed.
    log_queue_positions(session)
    assert _positions_by_job(session) == {
        str(head_job.id): [1],
        str(mid_job.id): [2],
        str(tail_job.id): [3],
    }

    # Emit-on-change against real SQL: a second pass reads back the rows it
    # just wrote and emits nothing.
    log_queue_positions(session)
    assert sum(len(positions) for positions in _positions_by_job(session).values()) == 3

    # The head job leaves the queue: everything behind it re-ranks...
    head_job.status = JobStatus.DELETED
    session.add(head_job)
    session.commit()
    log_queue_positions(session)
    assert _positions_by_job(session) == {
        str(head_job.id): [1],
        str(mid_job.id): [2, 1],
        str(tail_job.id): [3, 2],
    }

    # ...and the dedup compares each job against its *newest* row (log_date
    # descending), so a further pass over the re-ranked queue emits nothing.
    log_queue_positions(session)
    assert sum(len(positions) for positions in _positions_by_job(session).values()) == 5

    # The rows land in the activity feed, rendered at serve time.
    override_verify_token_as(admin_id)
    response = client.get(f"/api/model/{mid.id}/logs")
    assert response.status_code == 200
    body = response.json()
    assert {row["log"] for row in body} == {"Model Queued (2)", "Model Queued (1)"}
    assert all(row["globalRound"] is None for row in body)
