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

"""Integration coverage of the pre-running abort → net release flow (#787).

Exercises ``abort_model_training`` and ``release_scheduler_for_model`` against the throwaway
Postgres. Two things here are SQL-shaped and silently passable under mocked sessions: the
FLScheduler→FLJob join in ``release_scheduler_for_model``, and the assumption that
``FLScheduler.job_id`` survives the job's flip to DELETED (so the scheduler is still findable
after ``remove_job_from_queue``).
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from flip_api.db.models.main_models import FLJob, FLNets, FLScheduler, Model
from flip_api.domain.schemas.status import JobStatus, ModelStatus, NetStatus, ProjectStatus
from flip_api.domain.schemas.types import FLBackend
from flip_api.fl_services.services.fl_scheduler_service import release_scheduler_for_model
from flip_api.fl_services.services.fl_service import abort_model_training, get_fl_backend_job_id_by_model_id


@pytest.fixture
def scheduled_pre_running_job(session, user_factory, project_factory, model_factory):
    """A model in the pre-running window: INITIATED, job IN_PROGRESS, net BUSY, never submitted.

    Mirrors the state left by ``check_for_available_net`` + ``check_for_queued_jobs`` right
    before ``prepare_and_start_training`` completes: the scheduler is BUSY and pinned to the
    job, but ``fl_backend_job_id`` is still NULL.
    """
    user = user_factory()
    project = project_factory.build(owner_id=user.id, status=ProjectStatus.APPROVED, deleted=False)
    session.add(project)
    session.flush()

    model = model_factory.build(
        project_id=project.id,
        owner_id=user.id,
        status=ModelStatus.INITIATED,
        deleted=False,
    )
    session.add(model)
    session.flush()

    net = FLNets(name=f"net-{uuid4()}", endpoint=f"http://fl-api-{uuid4()}:5000", fl_backend=FLBackend.NVFLARE)
    session.add(net)
    session.flush()

    job = FLJob(model_id=model.id, status=JobStatus.IN_PROGRESS, fl_backend_job_id=None)
    session.add(job)
    session.flush()

    scheduler = FLScheduler(net_id=net.id, status=NetStatus.BUSY, job_id=job.id)
    session.add(scheduler)
    session.commit()

    return {"model": model, "job": job, "scheduler": scheduler, "net": net, "user": user, "project": project}


def test_abort_pre_running_job_deletes_job_and_frees_net(session, scheduled_pre_running_job):
    """Aborting a scheduled-but-never-submitted job DELETEs it and returns the net to AVAILABLE.

    This is the #787 core: before the fix the abort early-returned after the dequeue and the
    scheduler stayed BUSY until the stale-BUSY watchdog's next tick.
    """
    ctx = scheduled_pre_running_job

    abort_model_training(MagicMock(path_params={}), ctx["model"].id, session)

    session.expire_all()
    job = session.get(FLJob, ctx["job"].id)
    scheduler = session.get(FLScheduler, ctx["scheduler"].id)
    assert job is not None
    assert job.status == JobStatus.DELETED
    assert scheduler is not None
    assert scheduler.status == NetStatus.AVAILABLE
    assert scheduler.job_id is None


def test_release_scheduler_ignores_other_models_busy_net(session, scheduled_pre_running_job, model_factory):
    """Releasing for one model never frees a BUSY scheduler pinned to a different model's job."""
    ctx = scheduled_pre_running_job

    other_model = model_factory.build(
        project_id=ctx["project"].id,
        owner_id=ctx["user"].id,
        status=ModelStatus.INITIATED,
        deleted=False,
    )
    session.add(other_model)
    session.flush()

    released = release_scheduler_for_model(other_model.id, session)

    session.expire_all()
    scheduler = session.get(FLScheduler, ctx["scheduler"].id)
    assert released == 0
    assert scheduler is not None
    assert scheduler.status == NetStatus.BUSY
    assert scheduler.job_id == ctx["job"].id


def test_release_scheduler_finds_net_through_deleted_job(session, scheduled_pre_running_job):
    """The scheduler is still released when its job is already DELETED.

    ``FLScheduler.job_id`` survives the status flip, so the FLScheduler→FLJob join must still
    resolve the net after ``remove_job_from_queue`` ran.
    """
    ctx = scheduled_pre_running_job
    job = session.get(FLJob, ctx["job"].id)
    assert job is not None
    job.status = JobStatus.DELETED
    session.commit()

    released = release_scheduler_for_model(ctx["model"].id, session)

    session.expire_all()
    scheduler = session.get(FLScheduler, ctx["scheduler"].id)
    assert released == 1
    assert scheduler is not None
    assert scheduler.status == NetStatus.AVAILABLE
    assert scheduler.job_id is None


def test_abort_re_queued_model_with_job_history_frees_net(session, scheduled_pre_running_job):
    """A second pre-running abort works after a re-queue (abort → STOPPED → INITIATED → abort).

    The re-queue keeps the first attempt's DELETED job row, so the model holds two FLJob rows.
    The backend-job-id lookup must read only the newest one — with ``one_or_none`` it raised
    ``MultipleResultsFound``, which the narrowed except no longer swallows: the abort 500'd and
    the BUSY net leaked.
    """
    ctx = scheduled_pre_running_job

    earlier_attempt = FLJob(
        model_id=ctx["model"].id,
        status=JobStatus.DELETED,
        fl_backend_job_id=None,
        created=datetime.utcnow() - timedelta(minutes=5),
    )
    session.add(earlier_attempt)
    session.commit()

    abort_model_training(MagicMock(path_params={}), ctx["model"].id, session)

    session.expire_all()
    job = session.get(FLJob, ctx["job"].id)
    scheduler = session.get(FLScheduler, ctx["scheduler"].id)
    assert job is not None
    assert job.status == JobStatus.DELETED
    assert scheduler is not None
    assert scheduler.status == NetStatus.AVAILABLE
    assert scheduler.job_id is None


def test_backend_job_id_lookup_returns_newest_job(session, scheduled_pre_running_job):
    """With a stale DELETED row carrying an old backend id, the lookup returns the newest job's id."""
    ctx = scheduled_pre_running_job

    earlier_attempt = FLJob(
        model_id=ctx["model"].id,
        status=JobStatus.DELETED,
        fl_backend_job_id="stale-backend-job-id",
        created=datetime.utcnow() - timedelta(minutes=5),
    )
    session.add(earlier_attempt)
    current = session.get(FLJob, ctx["job"].id)
    assert current is not None
    current.fl_backend_job_id = "current-backend-job-id"
    session.commit()

    assert get_fl_backend_job_id_by_model_id(ctx["model"].id, session) == "current-backend-job-id"


def test_abort_stopped_model_ignores_late_results_upload(session, scheduled_pre_running_job):
    """After an abort, a late RESULTS_UPLOADED callback must not overwrite STOPPED (#787).

    The user decision is that a stopped model expects no results; only re-initiation
    (STOPPED → INITIATED) is allowed to move it on.
    """
    from flip_api.model_services.services.model_service import update_model_status

    ctx = scheduled_pre_running_job
    abort_model_training(MagicMock(path_params={}), ctx["model"].id, session)
    update_model_status(ctx["model"].id, ModelStatus.STOPPED, session)

    result = update_model_status(ctx["model"].id, ModelStatus.RESULTS_UPLOADED, session)

    session.expire_all()
    model = session.get(Model, ctx["model"].id)
    assert result == ModelStatus.STOPPED
    assert model is not None
    assert model.status == ModelStatus.STOPPED
