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

"""The cohort snapshot record's upsert against the real (project, trust) unique constraint (FLIP#857)."""

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from flip_api.db.models.main_models import CohortSnapshotStatus, TrustTask, XNATProjectStatus
from flip_api.domain.schemas.status import ProjectStatus, TaskStatus, TaskType, XNATImageStatus
from flip_api.private_services.snapshot_notifications import handle_snapshot_task_completed


@pytest.fixture
def project_and_trust(session, user_factory, project_factory, trust_factory):
    owner = user_factory()
    project = project_factory.build(owner_id=owner.id, status=ProjectStatus.APPROVED, deleted=False)
    trust = trust_factory.build()
    session.add(project)
    session.add(trust)
    session.commit()
    return project, trust


def _completed_task(project, trust, row_count: int, snapshot_at: str) -> TrustTask:
    return TrustTask(
        trust_id=trust.id,
        task_type=TaskType.PERSIST_COHORT,
        status=TaskStatus.COMPLETED,
        payload=json.dumps({"project_id": str(project.id), "trust_id": str(trust.id), "query_id": None}),
        result=json.dumps(
            {"row_count": row_count, "has_accessions": True, "snapshot_at": snapshot_at, "query_hash": "0" * 64}
        ),
    )


def test_a_second_snapshot_updates_the_one_row(session, project_and_trust):
    project, trust = project_and_trust

    handle_snapshot_task_completed(_completed_task(project, trust, 30, "2026-09-01T00:00:00+00:00"), session)
    handle_snapshot_task_completed(_completed_task(project, trust, 28, "2026-09-02T00:00:00+00:00"), session)

    rows = session.exec(
        select(CohortSnapshotStatus)
        .where(CohortSnapshotStatus.project_id == project.id)
        .where(CohortSnapshotStatus.trust_id == trust.id)
        .execution_options(populate_existing=True)
    ).all()
    assert len(rows) == 1
    assert rows[0].row_count == 28
    assert rows[0].snapshot_at.day == 2


def test_the_constraint_refuses_a_duplicate_row(session, project_and_trust):
    project, trust = project_and_trust
    for _ in range(2):
        session.add(
            CohortSnapshotStatus(project_id=project.id, trust_id=trust.id, row_count=1, snapshot_at=datetime.now(UTC))
        )

    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_a_late_first_freeze_reopens_the_reimport_budget_but_a_recheck_does_not(session, project_and_trust):
    """Imaging was created before the cohort froze at this trust, and the sweep spent its budget on refusals."""
    project, trust = project_and_trust
    status_row = XNATProjectStatus(
        xnat_project_id=uuid4(),
        project_id=project.id,
        trust_id=trust.id,
        retrieve_image_status=XNATImageStatus.CREATED,
        reimport_count=5,
    )
    session.add(status_row)
    session.commit()

    handle_snapshot_task_completed(_completed_task(project, trust, 30, "2026-09-01T00:00:00+00:00"), session)
    session.refresh(status_row)
    assert status_row.reimport_count == 0

    status_row.reimport_count = 3
    session.commit()
    handle_snapshot_task_completed(_completed_task(project, trust, 30, "2026-09-02T00:00:00+00:00"), session)
    session.refresh(status_row)
    assert status_row.reimport_count == 3
