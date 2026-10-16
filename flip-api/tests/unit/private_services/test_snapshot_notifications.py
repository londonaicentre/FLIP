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

"""Unit tests for PERSIST_COHORT post-processing (FLIP#857 audit record + drift surfacing)."""

import json
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from flip_api.db.models.main_models import (
    COHORT_SNAPSHOT_STATUS_UNIQUE,
    CohortSnapshotStatus,
    QueryStats,
    XNATProjectStatus,
)
from flip_api.domain.schemas.private import AggregatedCohortStats
from flip_api.domain.schemas.status import TaskStatus
from flip_api.private_services.snapshot_notifications import MALFORMED_RESULT_ERROR, handle_snapshot_task_completed

TRUST_ID = uuid4()
PROJECT_ID = str(uuid4())
QUERY_ID = str(uuid4())


def _make_task(row_count=24, query_id=QUERY_ID, result_overrides=None):
    task = MagicMock()
    task.id = uuid4()
    task.trust_id = TRUST_ID
    task.payload = json.dumps(
        {
            "project_id": PROJECT_ID,
            "trust_id": str(TRUST_ID),
            "encrypted_project_id": "enc",
            "query": "SELECT * FROM omop.image_occurrence",
            "query_id": query_id,
        }
    )
    result = {
        "row_count": row_count,
        "columns": ["modality", "accession_id"],
        "has_accessions": True,
        "snapshot_at": "2026-08-26T00:00:00+00:00",
        "query_hash": "abc123",
    }
    result.update(result_overrides or {})
    task.result = json.dumps(result)
    return task


def _make_db(stats_row=None, existing_record=False, xnat_status=None):
    """Session mock: exec() resolves each lookup by the model it selects; the upsert goes through execute()."""
    rows = {
        QueryStats: stats_row,
        CohortSnapshotStatus: uuid4() if existing_record else None,
        XNATProjectStatus: xnat_status,
    }

    def exec_(statement):
        result = MagicMock()
        result.first.return_value = rows[statement.column_descriptions[0]["entity"]]
        return result

    db = MagicMock()
    db.exec.side_effect = exec_
    return db


def _stats_row(trust_record_counts):
    stats = AggregatedCohortStats(record_count=sum(trust_record_counts.values()), trusts_results=[])
    stats.trust_record_counts = trust_record_counts
    row = MagicMock()
    row.stats = stats.model_dump_json()
    return row


def _upsert(db):
    """The single upsert statement the handler executed, compiled for Postgres."""
    db.execute.assert_called_once()
    return db.execute.call_args[0][0].compile(dialect=postgresql.dialect())


def test_records_the_frozen_cohort_audit_row():
    db = _make_db(stats_row=_stats_row({str(TRUST_ID): 24}))
    handle_snapshot_task_completed(_make_task(), db)

    params = _upsert(db).params
    assert str(params["project_id"]) == PROJECT_ID
    assert params["trust_id"] == TRUST_ID
    assert params["row_count"] == 24
    assert params["approved_record_count"] == 24
    assert params["has_accessions"] is True
    assert params["query_hash"] == "abc123"
    assert str(params["query_id"]) == QUERY_ID
    db.add.assert_not_called()
    db.commit.assert_called_once()


def test_membership_drift_is_surfaced_not_swallowed(caplog):
    """A frozen count that differs from the approved count logs a WARNING naming both."""
    db = _make_db(stats_row=_stats_row({str(TRUST_ID): 20}))
    with caplog.at_level("WARNING"):
        handle_snapshot_task_completed(_make_task(row_count=24), db)

    assert any("drift" in record.message and "20" in record.message for record in caplog.records)
    params = _upsert(db).params
    assert params["approved_record_count"] == 20
    assert params["row_count"] == 24


def test_missing_query_stats_still_records_the_row():
    """No aggregated stats (old data, errored trust) downgrades drift to not-comparable."""
    db = _make_db(stats_row=None)
    handle_snapshot_task_completed(_make_task(), db)

    params = _upsert(db).params
    assert params["approved_record_count"] is None
    assert params["row_count"] == 24


def test_a_requeued_snapshot_upserts_on_the_project_trust_constraint():
    """One row per (project, trust): a second snapshot updates the facts on conflict, never duplicates."""
    db = _make_db(stats_row=_stats_row({str(TRUST_ID): 30}))
    handle_snapshot_task_completed(_make_task(row_count=30), db)

    sql = str(_upsert(db))
    assert f"ON CONFLICT ON CONSTRAINT {COHORT_SNAPSHOT_STATUS_UNIQUE} DO UPDATE" in sql
    set_clause = sql.split("DO UPDATE SET", 1)[1]
    for column in ("row_count", "approved_record_count", "has_accessions", "query_hash", "snapshot_at", "query_id"):
        assert f"{column} = " in set_clause
    # The key and the first-write timestamp are never overwritten.
    for column in ("project_id", "trust_id", "created_at"):
        assert f"{column} = " not in set_clause


@pytest.mark.parametrize(
    "result",
    [
        None,
        "{not json",
        json.dumps({"row_count": 3, "snapshot_at": "2026-08-26T00:00:00+00:00", "query_hash": "x"}),
        json.dumps({"row_count": 3, "has_accessions": "yes", "snapshot_at": "2026-08-26", "query_hash": "x"}),
        json.dumps({"row_count": -1, "has_accessions": True, "snapshot_at": "2026-08-26", "query_hash": "x"}),
    ],
    ids=["no-result", "not-json", "missing-has_accessions", "non-bool-has_accessions", "negative-count"],
)
def test_a_malformed_result_fails_the_task_instead_of_retrying_forever(result):
    """It can never be recorded: retried, it would leave the trust pending with no way to re-queue it. A missing
    has_accessions is never defaulted to "tabular"."""
    task = _make_task()
    task.result = result
    task.status = TaskStatus.COMPLETED
    db = _make_db()

    handle_snapshot_task_completed(task, db)

    assert task.status == TaskStatus.FAILED
    assert json.loads(task.result) == {"error": MALFORMED_RESULT_ERROR}
    db.execute.assert_not_called()
    db.commit.assert_called_once()


def test_a_late_first_freeze_reopens_the_imaging_reimport_budget():
    """The freeze landed after imaging creation, which imported nothing: the sweep must get to pull the studies."""
    xnat_status = MagicMock(reimport_count=5)
    db = _make_db(xnat_status=xnat_status)

    handle_snapshot_task_completed(_make_task(), db)

    assert xnat_status.reimport_count == 0
    db.commit.assert_called_once()


@pytest.mark.parametrize(
    ("existing_record", "has_accessions"),
    [(True, True), (False, False)],
    ids=["re-check-of-a-frozen-trust", "tabular-cohort"],
)
def test_the_reimport_budget_is_left_alone_otherwise(existing_record, has_accessions):
    xnat_status = MagicMock(reimport_count=5)
    db = _make_db(existing_record=existing_record, xnat_status=xnat_status)

    handle_snapshot_task_completed(_make_task(result_overrides={"has_accessions": has_accessions}), db)

    assert xnat_status.reimport_count == 5
