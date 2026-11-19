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
"""Integration coverage of a job going back in the queue when the FL API cannot be reached (FLIP#1962).

Against the throwaway Postgres, because both behaviours are SQL-shaped and pass silently under a mocked session:
``requeue_job``'s UPDATE is conditional on ``IN_PROGRESS`` (a job aborted meanwhile must stay DELETED) and keeps
``created`` (the job keeps its place at the head of the queue); ``check_for_available_net`` leaves out the nets a pass
has already found not serving.
"""

from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlmodel import select

from flip_api.db.models.main_models import FLJob, FLNets, FLScheduler
from flip_api.domain.schemas.status import JobStatus, ModelStatus, NetStatus, ProjectStatus
from flip_api.domain.schemas.types import FLBackend
from flip_api.fl_services.services.fl_scheduler_service import (
    check_for_available_net,
    release_scheduler_for_model,
    requeue_job,
)


@pytest.fixture
def job_being_prepared(session, user_factory, project_factory, model_factory):
    """A job picked up an hour after it was queued: IN_PROGRESS, started, its net BUSY and pinned to it."""
    user = user_factory()
    project = project_factory.build(owner_id=user.id, status=ProjectStatus.APPROVED, deleted=False)
    session.add(project)
    session.flush()
    model = model_factory.build(project_id=project.id, owner_id=user.id, status=ModelStatus.INITIATED, deleted=False)
    session.add(model)
    session.flush()
    net = FLNets(name=f"net-{uuid4()}", endpoint=f"http://fl-api-{uuid4()}:8000", fl_backend=FLBackend.NVFLARE)
    session.add(net)
    session.flush()
    created = datetime.utcnow() - timedelta(hours=1)
    job = FLJob(model_id=model.id, status=JobStatus.IN_PROGRESS, created=created, started=datetime.utcnow())
    session.add(job)
    session.flush()
    scheduler = FLScheduler(net_id=net.id, status=NetStatus.BUSY, job_id=job.id)
    session.add(scheduler)
    session.commit()
    return {"model": model, "job": job, "net": net, "scheduler": scheduler, "created": created}


def test_a_requeued_job_is_queued_at_its_old_place_and_its_net_freed(session, job_being_prepared):
    ctx = job_being_prepared

    assert requeue_job(ctx["job"].id, session) is True
    assert release_scheduler_for_model(ctx["model"].id, session) == 1

    session.expire_all()
    job = session.get(FLJob, ctx["job"].id)
    assert job.status == JobStatus.QUEUED
    assert job.started is None
    assert job.created == ctx["created"]
    scheduler = session.get(FLScheduler, ctx["scheduler"].id)
    assert scheduler.status == NetStatus.AVAILABLE
    assert scheduler.job_id is None


def test_a_job_aborted_meanwhile_is_not_brought_back(session, job_being_prepared):
    ctx = job_being_prepared
    ctx["job"].status = JobStatus.DELETED
    session.commit()

    assert requeue_job(ctx["job"].id, session) is False

    session.expire_all()
    assert session.get(FLJob, ctx["job"].id).status == JobStatus.DELETED


def test_check_for_available_net_leaves_out_a_net_found_not_serving(session):
    # Any net already in the database (seeded, or left by another test) is excluded too, so only these two compete.
    already_there = list(session.exec(select(FLScheduler.net_id)).all())
    down, up = (
        FLNets(name=f"net-{uuid4()}", endpoint=f"http://fl-api-{uuid4()}:8000", fl_backend=FLBackend.NVFLARE)
        for _ in range(2)
    )
    session.add_all([down, up])
    session.flush()
    session.add_all([FLScheduler(net_id=down.id, status=NetStatus.AVAILABLE), FLScheduler(net_id=up.id)])
    session.commit()

    picked = check_for_available_net(session, exclude_net_ids=[*already_there, down.id])

    assert picked is not None
    assert picked.netId == up.id
    # The pick marked it BUSY, and the excluded net is never offered.
    assert check_for_available_net(session, exclude_net_ids=[*already_there, down.id]) is None
