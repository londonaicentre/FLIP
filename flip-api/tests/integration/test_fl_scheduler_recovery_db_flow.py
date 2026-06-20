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

"""Integration coverage of FL-net BUSY-scheduler recovery against real Postgres.

These exercise the raw SQL in ``_recover_stale_busy_schedulers`` and the
scheduler-freeing in ``remove_job_from_queue`` — both are SQL-shaped and pass
silently under a mocked session (the unit tests in ``test_run_jobs.py`` mock
``db.execute`` and so never run the actual ``WHERE`` clause).

Regression for the prod incident where a net was permanently starved: a
scheduler left ``BUSY`` pointing at a **soft-deleted** job (``status=DELETED``,
row still present in ``fl_job``) was never recovered, because the old recovery
query keyed on ``job_id NOT IN (SELECT id FROM fl_job)`` — which never matches a
soft-deleted row. ``run_jobs`` then logged "No available nets" every cycle for
24h+ and no newly-initiated model could ever be picked up.
"""

from datetime import datetime
from uuid import uuid4

from flip_api.db.models.main_models import FLJob, FLNets, FLScheduler, Model
from flip_api.domain.schemas.status import JobStatus, ModelStatus, NetStatus
from flip_api.domain.schemas.types import FLBackend
from flip_api.fl_services.run_jobs import _recover_stale_busy_schedulers
from flip_api.fl_services.services.fl_scheduler_service import remove_job_from_queue


def _make_net(session) -> FLNets:
    net = FLNets(name=f"net-{uuid4().hex[:8]}", endpoint=f"http://{uuid4().hex[:8]}:8000", fl_backend=FLBackend.NVFLARE)
    session.add(net)
    session.commit()
    session.refresh(net)
    return net


def _make_model(session) -> Model:
    model = Model(name="m", description="", status=ModelStatus.INITIATED, owner_id=uuid4())
    session.add(model)
    session.commit()
    session.refresh(model)
    return model


def _make_job(session, model_id, status: JobStatus, started=None, completed=None) -> FLJob:
    job = FLJob(model_id=model_id, status=status, started=started, completed=completed)
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def _busy_scheduler(session, net_id, job_id) -> FLScheduler:
    scheduler = FLScheduler(netid=net_id, status=NetStatus.BUSY, jobid=job_id)
    session.add(scheduler)
    session.commit()
    session.refresh(scheduler)
    return scheduler


def test_recovers_busy_scheduler_pointing_at_soft_deleted_job(session):
    """The incident: BUSY scheduler → DELETED (soft-deleted) job whose row still exists.

    The old query (``job_id NOT IN (SELECT id FROM fl_job)``) left this BUSY forever.
    """
    net = _make_net(session)
    model = _make_model(session)
    # A previously-aborted run: job soft-deleted (row remains), scheduler never freed.
    dead_job = _make_job(
        session, model.id, JobStatus.DELETED, started=datetime.utcnow(), completed=datetime.utcnow()
    )
    scheduler = _busy_scheduler(session, net.id, dead_job.id)

    recovered = _recover_stale_busy_schedulers(session)

    assert recovered == 1
    session.refresh(scheduler)
    assert scheduler.status == NetStatus.AVAILABLE
    assert scheduler.job_id is None


def test_recovers_busy_scheduler_with_completed_job(session):
    """A COMPLETED job must not keep a scheduler BUSY either."""
    net = _make_net(session)
    model = _make_model(session)
    done_job = _make_job(session, model.id, JobStatus.COMPLETED, completed=datetime.utcnow())
    scheduler = _busy_scheduler(session, net.id, done_job.id)

    recovered = _recover_stale_busy_schedulers(session)

    assert recovered == 1
    session.refresh(scheduler)
    assert scheduler.status == NetStatus.AVAILABLE
    assert scheduler.job_id is None


def test_does_not_recover_busy_scheduler_with_live_in_progress_job(session):
    """A legitimately-busy net (live IN_PROGRESS job) must be left alone."""
    net = _make_net(session)
    model = _make_model(session)
    live_job = _make_job(session, model.id, JobStatus.IN_PROGRESS, started=datetime.utcnow())
    scheduler = _busy_scheduler(session, net.id, live_job.id)

    recovered = _recover_stale_busy_schedulers(session)

    assert recovered == 0
    session.refresh(scheduler)
    assert scheduler.status == NetStatus.BUSY
    assert scheduler.job_id == live_job.id


def test_recovers_busy_scheduler_with_null_job(session):
    """BUSY with no job assigned (crash between mark-busy and job pickup) is recovered."""
    net = _make_net(session)
    scheduler = _busy_scheduler(session, net.id, None)

    recovered = _recover_stale_busy_schedulers(session)

    assert recovered == 1
    session.refresh(scheduler)
    assert scheduler.status == NetStatus.AVAILABLE


def test_remove_job_from_queue_frees_the_scheduler(session):
    """Aborting a run (soft-delete) must also free the net, not just delete the job."""
    net = _make_net(session)
    model = _make_model(session)
    job = _make_job(session, model.id, JobStatus.IN_PROGRESS, started=datetime.utcnow())
    scheduler = _busy_scheduler(session, net.id, job.id)

    remove_job_from_queue(model.id, session)

    session.refresh(job)
    session.refresh(scheduler)
    assert job.status == JobStatus.DELETED
    assert scheduler.status == NetStatus.AVAILABLE
    assert scheduler.job_id is None
