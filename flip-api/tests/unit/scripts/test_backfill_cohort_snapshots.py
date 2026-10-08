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

"""Unit tests for the cohort-snapshot backfill (FLIP#857)."""

import json
from collections.abc import Generator
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from flip_api.db.models.main_models import CohortSnapshotStatus, Trust
from flip_api.domain.schemas.status import CohortSnapshotState
from flip_api.project_services.services.cohort_snapshot_service import TrustSnapshotState
from flip_api.scripts import backfill_cohort_snapshots as backfill

MODULE = "flip_api.scripts.backfill_cohort_snapshots"
PROJECT_A, PROJECT_B = uuid4(), uuid4()
RECORDED = Trust(id=uuid4(), name="Recorded")
PENDING = Trust(id=uuid4(), name="Pending")
MISSING = Trust(id=uuid4(), name="Missing")
FAILED_NO_RECORD = Trust(id=uuid4(), name="Failed")


def _record(trust_id):
    return CohortSnapshotStatus(
        project_id=PROJECT_A, trust_id=trust_id, row_count=5, has_accessions=False, snapshot_at=datetime.now(UTC)
    )


STATES = {
    RECORDED.id: TrustSnapshotState(trust=RECORDED, state=CohortSnapshotState.FROZEN, record=_record(RECORDED.id)),
    PENDING.id: TrustSnapshotState(trust=PENDING, state=CohortSnapshotState.PENDING),
    MISSING.id: TrustSnapshotState(trust=MISSING, state=CohortSnapshotState.FAILED, error="not requested"),
    FAILED_NO_RECORD.id: TrustSnapshotState(trust=FAILED_NO_RECORD, state=CohortSnapshotState.FAILED, error="x"),
}
TRUSTS = {PROJECT_A: [RECORDED, PENDING, MISSING], PROJECT_B: [FAILED_NO_RECORD]}


@pytest.fixture
def session() -> MagicMock:
    session = MagicMock()
    session.exec.return_value.all.return_value = [PROJECT_A, PROJECT_B]
    return session


@pytest.fixture
def seams() -> Generator[SimpleNamespace, None, None]:
    with (
        patch(f"{MODULE}.get_approved_trusts_for_project", side_effect=lambda pid, db: TRUSTS[pid]),
        patch(
            f"{MODULE}.resolve_snapshot_states",
            side_effect=lambda pid, trusts, db: [STATES[trust.id] for trust in trusts],
        ),
        patch(f"{MODULE}.queue_cohort_snapshot") as queue,
    ):
        yield SimpleNamespace(queue=queue)


def test_queues_every_approved_trust_without_a_record(session: MagicMock, seams: SimpleNamespace):
    outcomes = backfill.backfill_cohort_snapshots(session)

    queued = {(call.args[0], call.args[1].id) for call in seams.queue.call_args_list}
    assert queued == {(PROJECT_A, MISSING.id), (PROJECT_B, FAILED_NO_RECORD.id)}
    assert {o["trust_name"]: o["outcome"] for o in outcomes} == {"Missing": "queued", "Failed": "queued"}


def test_include_frozen_also_queues_trusts_with_a_record(session: MagicMock, seams: SimpleNamespace):
    """The fleet-wide recovery for a trust that lost its store; a pending trust is still skipped."""
    backfill.backfill_cohort_snapshots(session, include_frozen=True)

    queued = {(call.args[0], call.args[1].id) for call in seams.queue.call_args_list}
    assert queued == {(PROJECT_A, RECORDED.id), (PROJECT_A, MISSING.id), (PROJECT_B, FAILED_NO_RECORD.id)}


def test_selects_only_approved_live_projects(session: MagicMock, seams: SimpleNamespace):
    backfill.backfill_cohort_snapshots(session)

    sql = str(session.exec.call_args[0][0].compile(compile_kwargs={"literal_binds": True}))
    assert "projects.status = 'APPROVED'" in sql
    assert "projects.deleted IS false" in sql


def test_dry_run_queues_nothing(session: MagicMock, seams: SimpleNamespace):
    outcomes = backfill.backfill_cohort_snapshots(session, dry_run=True)

    seams.queue.assert_not_called()
    assert {o["outcome"] for o in outcomes} == {"would_queue"}
    assert len(outcomes) == 2


def test_a_refused_trust_is_reported_and_the_rest_continue(session: MagicMock, seams: SimpleNamespace):
    def queue(project_id, trust, db):
        if trust.id == MISSING.id:
            raise HTTPException(status_code=409, detail="Project has no cohort query")
        return {"success": "queued"}

    seams.queue.side_effect = queue

    outcomes = {o["trust_name"]: o for o in backfill.backfill_cohort_snapshots(session)}

    assert outcomes["Missing"]["outcome"] == "failed"
    assert outcomes["Missing"]["reason"] == "Project has no cohort query"
    assert outcomes["Failed"]["outcome"] == "queued"


def test_main_prints_a_json_summary(monkeypatch, capsys, seams: SimpleNamespace):
    monkeypatch.setattr("sys.argv", ["backfill_cohort_snapshots", "--dry-run"])
    with (
        patch(f"{MODULE}.get_engine"),
        patch(f"{MODULE}.Session") as session_cls,
    ):
        session_cls.return_value.__enter__.return_value.exec.return_value.all.return_value = [PROJECT_A]
        backfill.main()

    summary = json.loads(capsys.readouterr().out)
    assert summary["counts"] == {"would_queue": 1}
    seams.queue.assert_not_called()


def test_the_script_imports_in_a_fresh_interpreter():
    """``make backfill_cohort_snapshots`` runs it with ``python -m``: under pytest the app is already
    imported, which hid a circular import that only a clean interpreter hits."""
    import os
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-c", "import flip_api.scripts.backfill_cohort_snapshots"],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
