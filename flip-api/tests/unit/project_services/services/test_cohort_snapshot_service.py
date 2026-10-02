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

"""Unit tests for the per-trust cohort freeze state (FLIP#857)."""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from flip_api.db.models.main_models import CohortSnapshotStatus, Trust, TrustTask
from flip_api.domain.schemas.status import CohortSnapshotState, TaskStatus, TaskType
from flip_api.project_services.services import cohort_snapshot_service as service

PROJECT_ID = uuid4()
T0 = datetime(2026, 9, 1, tzinfo=UTC)


def make_task(trust_id, status, result=None, created_at=T0):
    return TrustTask(
        trust_id=trust_id,
        task_type=TaskType.PERSIST_COHORT,
        payload="{}",
        query_id=uuid4(),
        status=status,
        result=json.dumps(result) if result is not None else None,
        created_at=created_at,
    )


def make_record(trust_id):
    return CohortSnapshotStatus(
        project_id=PROJECT_ID, trust_id=trust_id, row_count=12, has_accessions=True, snapshot_at=T0
    )


def make_db(tasks, records):
    """exec() answers the task lookup first, then the record lookup."""
    db = MagicMock()
    task_result, record_result = MagicMock(), MagicMock()
    task_result.all.return_value = tasks
    record_result.all.return_value = records
    db.exec.side_effect = [task_result, record_result]
    return db


@pytest.mark.parametrize(
    ("task_status", "has_record", "expected"),
    [
        (TaskStatus.PENDING, False, CohortSnapshotState.PENDING),
        (TaskStatus.IN_PROGRESS, False, CohortSnapshotState.PENDING),
        (TaskStatus.COMPLETED, True, CohortSnapshotState.FROZEN),
        # Completed but its record not yet written: post-processing is still to run.
        (TaskStatus.COMPLETED, False, CohortSnapshotState.PENDING),
        (TaskStatus.FAILED, False, CohortSnapshotState.FAILED),
        (TaskStatus.CANCELLED, False, CohortSnapshotState.FAILED),
        # A re-check in flight is reported as such, even over an older record.
        (TaskStatus.PENDING, True, CohortSnapshotState.PENDING),
    ],
)
def test_state_follows_the_latest_task(task_status, has_record, expected):
    trust = Trust(id=uuid4(), name="GSTT")
    records = [make_record(trust.id)] if has_record else []
    db = make_db([make_task(trust.id, task_status)], records)

    (entry,) = service.resolve_snapshot_states(PROJECT_ID, [trust], db)

    assert entry.state == expected
    assert (entry.record is not None) == has_record
    assert (entry.error is not None) == (expected == CohortSnapshotState.FAILED)


@pytest.mark.parametrize("task_status", [TaskStatus.FAILED, TaskStatus.CANCELLED])
def test_a_failed_recheck_of_a_frozen_trust_stays_frozen_with_a_warning(task_status):
    """The hub cannot tell a trust that still serves its membership from one that lost it: it keeps the record's
    FROZEN state and says the re-check failed, never claiming training will be refused."""
    trust = Trust(id=uuid4(), name="GSTT")
    task = make_task(trust.id, task_status, result={"error": "Exceeded maximum retries (3)"})
    db = make_db([task], [make_record(trust.id)])

    (entry,) = service.resolve_snapshot_states(PROJECT_ID, [trust], db)

    assert entry.state == CohortSnapshotState.FROZEN
    assert entry.record is not None
    assert entry.error is not None
    assert entry.error.startswith("The last re-check failed")


def test_a_trust_never_asked_is_failed_not_requested():
    """A project approved before snapshots existed: no task, no record — the trust refuses it."""
    trust = Trust(id=uuid4(), name="GSTT")
    db = make_db([], [])

    (entry,) = service.resolve_snapshot_states(PROJECT_ID, [trust], db)

    assert entry.state == CohortSnapshotState.FAILED
    assert entry.error == service.NOT_REQUESTED


def test_a_record_without_a_task_is_frozen():
    trust = Trust(id=uuid4(), name="GSTT")
    db = make_db([], [make_record(trust.id)])

    (entry,) = service.resolve_snapshot_states(PROJECT_ID, [trust], db)

    assert entry.state == CohortSnapshotState.FROZEN


def test_only_the_newest_task_per_trust_counts():
    """Tasks arrive newest first; an older failure does not mask a newer success."""
    trust = Trust(id=uuid4(), name="GSTT")
    newer = make_task(trust.id, TaskStatus.COMPLETED, created_at=T0 + timedelta(hours=1))
    older = make_task(trust.id, TaskStatus.FAILED, result={"error": "x", "status_code": 403})
    db = make_db([newer, older], [make_record(trust.id)])

    (entry,) = service.resolve_snapshot_states(PROJECT_ID, [trust], db)

    assert entry.state == CohortSnapshotState.FROZEN


def test_one_entry_per_trust_in_the_order_given():
    frozen, pending, never = (Trust(id=uuid4(), name=name) for name in ("A", "B", "C"))
    db = make_db(
        [make_task(frozen.id, TaskStatus.COMPLETED), make_task(pending.id, TaskStatus.PENDING)],
        [make_record(frozen.id)],
    )

    entries = service.resolve_snapshot_states(PROJECT_ID, [frozen, pending, never], db)

    assert [(e.trust.name, e.state) for e in entries] == [
        ("A", CohortSnapshotState.FROZEN),
        ("B", CohortSnapshotState.PENDING),
        ("C", CohortSnapshotState.FAILED),
    ]


def test_no_trusts_queries_nothing():
    db = MagicMock()

    assert service.resolve_snapshot_states(PROJECT_ID, [], db) == []
    db.exec.assert_not_called()


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ({"error": "Cohort below threshold for patient 1234", "status_code": 403}, service.REFUSED),
        ({"error": "syntax error at or near SELEC", "status_code": 400}, service.REJECTED),
        ({"error": "validation", "status_code": 422}, service.REJECTED),
        ({"error": "Exceeded maximum retries (3)"}, service.TIMED_OUT),
        ({"error": "Malformed cohort snapshot result"}, service.MALFORMED),
        ({"error": "psycopg2.OperationalError: host omop-db-1 port 5432"}, service.TRUST_ERROR),
        (None, service.TRUST_ERROR),
    ],
)
def test_failure_category_never_passes_the_trusts_error_text_through(result, expected):
    task = make_task(uuid4(), TaskStatus.FAILED, result=result)

    category = service.failure_category(task)

    assert category == expected
    if result:
        assert result["error"] not in category


def test_failure_category_survives_an_unparseable_result():
    task = make_task(uuid4(), TaskStatus.FAILED)
    task.result = "not json"

    assert service.failure_category(task) == service.TRUST_ERROR


def test_failure_category_survives_a_result_that_is_not_an_object():
    task = make_task(uuid4(), TaskStatus.FAILED)
    task.result = '["status_code", 403]'

    assert service.failure_category(task) == service.TRUST_ERROR


def test_cancelled_task_category():
    assert service.failure_category(make_task(uuid4(), TaskStatus.CANCELLED)) == service.CANCELLED
