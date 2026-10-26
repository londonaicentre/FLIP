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

"""Per-job cohort data staging at the trusts (FLIP#1678).

Before a job is submitted, each of its trusts writes the job's approved dataframe where the job's pods mount it, and
the job does not start until every trust has. Once the model ends, the trusts remove it. Both travel as TrustTasks:
the hub never reaches a trust, the trusts poll.
"""

import json
import time
from collections.abc import Sequence
from uuid import UUID

from sqlmodel import Session, col, select

from flip_api.config import get_settings
from flip_api.db.models.main_models import FLJob, Model, Trust, TrustTask
from flip_api.domain.interfaces.trust import IJobDataTask
from flip_api.domain.schemas.status import TaskStatus, TaskType
from flip_api.utils.encryption import PROJECT_ID_CONTEXT, encrypt
from flip_api.utils.logger import logger

_REASON_LENGTH = 300


class JobDataStagingError(Exception):
    """A trust did not stage a job's cohort data, so the job must not start."""


def queue_job_data_tasks(
    task_type: TaskType, model_id: UUID, project_id: UUID, trust_ids: Sequence[UUID], session: Session
) -> list[UUID]:
    """Queue one stage or unstage task per trust.

    Args:
        task_type (TaskType): ``STAGE_JOB_DATA`` or ``UNSTAGE_JOB_DATA``.
        model_id (UUID): The model whose job's data it is.
        project_id (UUID): The model's project.
        trust_ids (Sequence[UUID]): The trusts of the job.
        session (Session): The database session.

    Returns:
        list[UUID]: The queued tasks' ids, in ``trust_ids`` order.
    """
    encrypted_project_id = encrypt(str(project_id), context=PROJECT_ID_CONTEXT)
    tasks = [
        TrustTask(
            trust_id=trust_id,
            task_type=task_type,
            payload=json.dumps(
                IJobDataTask(
                    project_id=project_id,
                    trust_id=trust_id,
                    model_id=model_id,
                    encrypted_project_id=encrypted_project_id,
                ).model_dump(mode="json")
            ),
        )
        for trust_id in trust_ids
    ]
    session.add_all(tasks)
    session.commit()
    return [task.id for task in tasks]


def _reason(task: TrustTask) -> str:
    try:
        error = json.loads(task.result or "{}").get("error")
    except (ValueError, AttributeError):
        error = None
    return str(error or task.status.value)[:_REASON_LENGTH]


def _describe(tasks: Sequence[TrustTask], session: Session, what: str) -> str:
    names = []
    for task in tasks:
        trust = session.get(Trust, task.trust_id)
        names.append(f"{trust.name if trust else task.trust_id}: {_reason(task)}")
    return f"{what} ({'; '.join(names)})"


def wait_for_trust_tasks(
    task_ids: Sequence[UUID], session: Session, timeout_seconds: float, poll_seconds: float = 2.0
) -> None:
    """Block until every task has completed.

    On a timeout the tasks no trust has picked up yet are cancelled, so a trust that comes back later never acts on
    them.

    Args:
        task_ids (Sequence[UUID]): The tasks to wait for.
        session (Session): The database session.
        timeout_seconds (float): How long to wait in all.
        poll_seconds (float): How often to re-read the tasks.

    Raises:
        JobDataStagingError: naming each trust, when a task failed or was cancelled, or the wait timed out.
    """
    deadline = time.monotonic() + timeout_seconds
    while True:
        session.expire_all()
        tasks = session.exec(select(TrustTask).where(col(TrustTask.id).in_(task_ids))).all()
        failed = [t for t in tasks if t.status in (TaskStatus.FAILED, TaskStatus.CANCELLED)]
        if failed:
            raise JobDataStagingError(_describe(failed, session, "A trust could not stage the job's data"))
        waiting = [t for t in tasks if t.status != TaskStatus.COMPLETED]
        if not waiting and len(tasks) == len(task_ids):
            return
        if time.monotonic() >= deadline:
            for task in waiting:
                if task.status == TaskStatus.PENDING:
                    task.status = TaskStatus.CANCELLED
            session.commit()
            raise JobDataStagingError(
                _describe(waiting, session, f"Trusts did not stage the job's data within {timeout_seconds:g}s")
            )
        time.sleep(poll_seconds)


def _project_of(model_id: UUID, session: Session) -> UUID | None:
    model = session.get(Model, model_id)
    return model.project_id if model is not None else None


def stage_job_data(model_id: UUID, trust_ids: Sequence[UUID], session: Session) -> None:
    """Have every trust of a job stage its cohort data, and wait until they all have.

    Args:
        model_id (UUID): The model whose job is about to be submitted.
        trust_ids (Sequence[UUID]): The trusts of the job.
        session (Session): The database session.

    Raises:
        JobDataStagingError: when a trust could not stage the data, or did not in time.
    """
    project_id = _project_of(model_id, session)
    if project_id is None:
        raise JobDataStagingError(f"Model {model_id} has no project, so there is no cohort to stage")
    task_ids = queue_job_data_tasks(TaskType.STAGE_JOB_DATA, model_id, project_id, trust_ids, session)
    wait_for_trust_tasks(task_ids, session, get_settings().STAGE_JOB_DATA_TIMEOUT_SECONDS)


def unstage_job_data(model_id: UUID, session: Session) -> None:
    """Ask every trust of the model's newest job to remove that job's cohort data. Never raises.

    A model that never had a job queues nothing. Failure here must not stop the model's terminal status: the
    trusts' own sweep removes data that is never unstaged.

    Args:
        model_id (UUID): The model that has ended.
        session (Session): The database session.
    """
    try:
        project_id = _project_of(model_id, session)
        job = session.exec(select(FLJob).where(FLJob.model_id == model_id).order_by(col(FLJob.created).desc())).first()
        if project_id is None or job is None or not job.trusts:
            return
        queue_job_data_tasks(TaskType.UNSTAGE_JOB_DATA, model_id, project_id, [t.id for t in job.trusts], session)
    except Exception:
        logger.exception(f"Could not queue the removal of model {model_id}'s cohort data")
        session.rollback()
