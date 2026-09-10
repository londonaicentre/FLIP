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

from flip_api.domain.schemas.private import AggregatedCohortStats
from flip_api.private_services.snapshot_notifications import handle_snapshot_task_completed

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


def _make_db(stats_row=None, existing_status=None):
    """Session mock: first exec() resolves QueryStats, second the existing status row."""
    db = MagicMock()
    stats_result = MagicMock()
    stats_result.first.return_value = stats_row
    status_result = MagicMock()
    status_result.first.return_value = existing_status
    db.exec.side_effect = [stats_result, status_result]
    return db


def _stats_row(trust_record_counts):
    stats = AggregatedCohortStats(record_count=sum(trust_record_counts.values()), trusts_results=[])
    stats.trust_record_counts = trust_record_counts
    row = MagicMock()
    row.stats = stats.model_dump_json()
    return row


def test_records_the_frozen_cohort_audit_row():
    db = _make_db(stats_row=_stats_row({str(TRUST_ID): 24}))
    handle_snapshot_task_completed(_make_task(), db)

    db.add.assert_called_once()
    status_row = db.add.call_args[0][0]
    assert status_row.row_count == 24
    assert status_row.approved_record_count == 24
    assert status_row.has_accessions is True
    assert status_row.query_hash == "abc123"
    assert str(status_row.query_id) == QUERY_ID
    db.commit.assert_called_once()


def test_membership_drift_is_surfaced_not_swallowed(caplog):
    """A frozen count that differs from the approved count logs a WARNING naming both."""
    db = _make_db(stats_row=_stats_row({str(TRUST_ID): 20}))
    with caplog.at_level("WARNING"):
        handle_snapshot_task_completed(_make_task(row_count=24), db)

    assert any("drift" in record.message and "20" in record.message for record in caplog.records)
    status_row = db.add.call_args[0][0]
    assert status_row.approved_record_count == 20
    assert status_row.row_count == 24


def test_missing_query_stats_still_records_the_row():
    """No aggregated stats (old data, errored trust) downgrades drift to not-comparable."""
    db = _make_db(stats_row=None)
    handle_snapshot_task_completed(_make_task(), db)

    status_row = db.add.call_args[0][0]
    assert status_row.approved_record_count is None
    assert status_row.row_count == 24


def test_reapproval_updates_the_existing_row_in_place():
    existing = MagicMock()
    db = _make_db(stats_row=_stats_row({str(TRUST_ID): 30}), existing_status=existing)
    handle_snapshot_task_completed(_make_task(row_count=30), db)

    # The same row object is updated and re-added — no duplicate per (project, trust).
    assert db.add.call_args[0][0] is existing
    assert existing.row_count == 30


def test_task_without_result_raises():
    task = _make_task()
    task.result = None
    with pytest.raises(ValueError, match="no result data"):
        handle_snapshot_task_completed(task, MagicMock())
