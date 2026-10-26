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

"""Per-job cohort data staging at the trusts (FLIP#1678), against a real Postgres."""

import json
from uuid import uuid4

import pytest
from sqlmodel import col, select

from flip_api.db.models.main_models import TrustTask
from flip_api.domain.schemas.status import TaskStatus, TaskType
from flip_api.fl_services.services.job_data_staging import (
    JobDataStagingError,
    queue_job_data_tasks,
    unstage_job_data,
    wait_for_trust_tasks,
)
from flip_api.utils.encryption import PROJECT_ID_CONTEXT, decrypt
from tests.integration.test_all_models_endpoint import _add_model, _add_project, _add_trust, _dispatch_run


@pytest.fixture
def staged_model(session, project_factory, model_factory, trust_factory):
    owner = uuid4()
    project = _add_project(session, project_factory, owner_id=owner)
    model = _add_model(session, model_factory, project_id=project.id, owner_id=owner)
    trusts = [
        _add_trust(session, trust_factory, name="Trust_1", code="T1"),
        _add_trust(session, trust_factory, name="Trust_2", code="T2"),
    ]
    return {"project": project, "model": model, "trusts": trusts}


def _tasks(session, task_ids):
    session.expire_all()
    return session.exec(select(TrustTask).where(col(TrustTask.id).in_(task_ids))).all()


def _set_status(session, task_id, status, result=None):
    task = session.get(TrustTask, task_id)
    task.status = status
    task.result = result
    session.commit()


def test_queue_job_data_tasks_creates_one_pending_task_per_trust(session, staged_model):
    model, project, trusts = staged_model["model"], staged_model["project"], staged_model["trusts"]

    task_ids = queue_job_data_tasks(TaskType.STAGE_JOB_DATA, model.id, project.id, [t.id for t in trusts], session)

    tasks = _tasks(session, task_ids)
    assert {t.trust_id for t in tasks} == {t.id for t in trusts}
    for task in tasks:
        assert task.task_type == TaskType.STAGE_JOB_DATA
        assert task.status == TaskStatus.PENDING
        payload = json.loads(task.payload)
        assert payload["model_id"] == str(model.id)
        assert payload["project_id"] == str(project.id)
        assert payload["trust_id"] == str(task.trust_id)
        assert decrypt(payload["encrypted_project_id"], context=PROJECT_ID_CONTEXT) == str(project.id)


def test_wait_returns_when_every_task_completes(session, staged_model):
    model, project, trusts = staged_model["model"], staged_model["project"], staged_model["trusts"]
    task_ids = queue_job_data_tasks(TaskType.STAGE_JOB_DATA, model.id, project.id, [t.id for t in trusts], session)
    for task_id in task_ids:
        _set_status(session, task_id, TaskStatus.COMPLETED)

    wait_for_trust_tasks(task_ids, session, timeout_seconds=1, poll_seconds=0.01)


def test_wait_raises_naming_the_trust_on_a_failed_task(session, staged_model):
    model, project, trusts = staged_model["model"], staged_model["project"], staged_model["trusts"]
    task_ids = queue_job_data_tasks(TaskType.STAGE_JOB_DATA, model.id, project.id, [trusts[0].id], session)
    _set_status(
        session,
        task_ids[0],
        TaskStatus.FAILED,
        json.dumps({"error": "Cohort is too small for row-level data to be released.", "status_code": 403}),
    )

    with pytest.raises(JobDataStagingError) as excinfo:
        wait_for_trust_tasks(task_ids, session, timeout_seconds=1, poll_seconds=0.01)

    assert "Trust_1" in str(excinfo.value)
    assert "Cohort is too small for row-level data to be released." in str(excinfo.value)


def test_wait_times_out_and_cancels_pending_tasks(session, staged_model):
    # Review Focus 2: an offline trust never picks its task up. The model fails, and the still-PENDING task is
    # cancelled so that trust cannot stage the job's data when it comes back.
    model, project, trusts = staged_model["model"], staged_model["project"], staged_model["trusts"]
    task_ids = queue_job_data_tasks(TaskType.STAGE_JOB_DATA, model.id, project.id, [t.id for t in trusts], session)
    _set_status(session, task_ids[0], TaskStatus.COMPLETED)

    with pytest.raises(JobDataStagingError) as excinfo:
        wait_for_trust_tasks(task_ids, session, timeout_seconds=0.05, poll_seconds=0.01)

    statuses = {t.trust_id: t.status for t in _tasks(session, task_ids)}
    assert statuses[trusts[0].id] == TaskStatus.COMPLETED
    assert statuses[trusts[1].id] == TaskStatus.CANCELLED
    assert "Trust_2" in str(excinfo.value)


def test_unstage_after_failed_stage_targets_every_job_trust(session, staged_model):
    # Review Focus 3: whatever the stage did, every trust of the job is asked to clean up.
    model, trusts = staged_model["model"], staged_model["trusts"]
    _dispatch_run(session, model_id=model.id, trusts=trusts)

    unstage_job_data(model.id, session)

    session.expire_all()
    tasks = session.exec(select(TrustTask).where(TrustTask.task_type == TaskType.UNSTAGE_JOB_DATA)).all()
    assert {t.trust_id for t in tasks} == {t.id for t in trusts}
    assert {json.loads(t.payload)["model_id"] for t in tasks} == {str(model.id)}


def test_unstage_without_a_job_queues_nothing(session, staged_model):
    unstage_job_data(staged_model["model"].id, session)

    assert session.exec(select(TrustTask)).all() == []
