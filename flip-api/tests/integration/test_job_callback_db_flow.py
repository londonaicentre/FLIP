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

"""Integration coverage of the guard on the FL server's callback routes, against the throwaway Postgres.

Which job may report on a model is decided from the model's ``fl_job`` rows, their credential hashes and the
model's status, and a status callback then rewrites those rows (``update_fl_scheduler``), so the sequence is
only meaningful on real rows.
"""

from collections.abc import Generator
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, col, select

from flip_api.auth.access_manager import authenticate_internal_service
from flip_api.auth.job_callback import JOB_CREDENTIAL_HEADER, hash_job_credential
from flip_api.db.models.main_models import FLJob, FLLogs, FLNets, FLScheduler, Model
from flip_api.domain.schemas.status import JobStatus, ModelStatus, NetStatus, ProjectStatus
from flip_api.domain.schemas.types import FLBackend
from flip_api.main import app

REFUSAL = {"detail": "Not authorised for this model."}
CREDENTIAL = "this-job"  # pragma: allowlist secret
AUTH = {JOB_CREDENTIAL_HEADER: CREDENTIAL}


@pytest.fixture
def fl_server() -> Generator[TestClient, None, None]:
    """A caller holding the internal service key, as every job's server-side process does today."""
    app.dependency_overrides[authenticate_internal_service] = lambda: None
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.pop(authenticate_internal_service, None)


@pytest.fixture
def make_model(session, user_factory, project_factory, model_factory):
    """Create a model in ``status`` with one job per entry of ``job_statuses``, oldest first.

    Every job is given ``credential``, so a refusal in these tests is never for want of one.
    """

    def _make(status: ModelStatus, job_statuses: list[JobStatus], credential: str = CREDENTIAL) -> Model:
        user = user_factory()
        project = project_factory.build(owner_id=user.id, status=ProjectStatus.APPROVED, deleted=False)
        session.add(project)
        session.flush()
        model = model_factory.build(project_id=project.id, owner_id=user.id, status=status, deleted=False)
        session.add(model)
        session.flush()
        oldest = datetime.utcnow() - timedelta(hours=len(job_statuses))
        for age, job_status in enumerate(job_statuses):
            session.add(
                FLJob(
                    model_id=model.id,
                    status=job_status,
                    created=oldest + timedelta(hours=age),
                    credential_hash=hash_job_credential(credential),
                )
            )
        session.commit()
        return model

    return _make


def _status(fl_server: TestClient, model_id: UUID, status: ModelStatus, credential: str = CREDENTIAL) -> int:
    headers = {JOB_CREDENTIAL_HEADER: credential}
    return fl_server.put(f"/api/model/{model_id}/status/{status.value}", headers=headers).status_code


def _log(fl_server: TestClient, model_id: UUID, credential: str = CREDENTIAL) -> int:
    headers = {JOB_CREDENTIAL_HEADER: credential}
    body = {"log": "line", "success": False}
    return fl_server.post(f"/api/model/{model_id}/logs", json=body, headers=headers).status_code


def _job_statuses(session: Session, model_id: UUID) -> list[JobStatus]:
    session.expire_all()
    jobs = session.exec(select(FLJob).where(FLJob.model_id == model_id).order_by(col(FLJob.created))).all()
    return [job.status for job in jobs]


def _log_count(session: Session, model_id: UUID) -> int:
    return len(session.exec(select(FLLogs).where(FLLogs.model_id == model_id)).all())


@pytest.mark.parametrize(
    ("model_status", "job_statuses"),
    [
        (ModelStatus.RESULTS_UPLOADED, [JobStatus.COMPLETED]),
        (ModelStatus.STOPPED, [JobStatus.DELETED]),
        (ModelStatus.ERROR, [JobStatus.DELETED]),
        (ModelStatus.INITIATED, [JobStatus.QUEUED]),
        (ModelStatus.ERROR, [JobStatus.COMPLETED, JobStatus.QUEUED]),
        (ModelStatus.PENDING, []),
    ],
)
def test_model_with_no_reporting_job_is_left_untouched(fl_server, session, make_model, model_status, job_statuses):
    model = make_model(model_status, job_statuses)

    for new_status in (ModelStatus.ERROR, ModelStatus.STOPPED, ModelStatus.RESULTS_UPLOADED, ModelStatus.RUNNING):
        response = fl_server.put(f"/api/model/{model.id}/status/{new_status.value}", headers=AUTH)
        assert (response.status_code, response.json()) == (403, REFUSAL)
    assert fl_server.post(f"/api/model/{model.id}/logs", json={"log": "line"}, headers=AUTH).json() == REFUSAL
    metric = {"fl_client_name": "Trust_1", "global_round": 1, "label": "loss", "value": 0.5}
    assert fl_server.post(f"/api/model/{model.id}/metrics", json=metric, headers=AUTH).json() == REFUSAL

    session.expire_all()
    assert session.get(Model, model.id).status == model_status
    assert _job_statuses(session, model.id) == job_statuses
    assert _log_count(session, model.id) == 0


def test_unknown_model_gets_the_same_refusal(fl_server):
    response = fl_server.put(f"/api/model/{uuid4()}/status/{ModelStatus.ERROR.value}", headers=AUTH)

    assert (response.status_code, response.json()) == (403, REFUSAL)


def test_job_reports_on_its_own_model_and_no_other(fl_server, session, make_model):
    """Two jobs running at once, each on its own net: neither can write to the other's model."""
    mine = make_model(ModelStatus.RUNNING, [JobStatus.IN_PROGRESS], credential="mine")
    theirs = make_model(ModelStatus.RUNNING, [JobStatus.IN_PROGRESS], credential="theirs")

    for new_status in (ModelStatus.ERROR, ModelStatus.STOPPED, ModelStatus.RESULTS_UPLOADED):
        assert _status(fl_server, theirs.id, new_status, credential="mine") == 403
    assert _log(fl_server, theirs.id, credential="mine") == 403
    metric = {"fl_client_name": "Trust_1", "global_round": 1, "label": "loss", "value": 0.5}
    headers = {JOB_CREDENTIAL_HEADER: "mine"}
    assert fl_server.post(f"/api/model/{theirs.id}/metrics", json=metric, headers=headers).status_code == 403
    # The internal service key alone, which every job's process holds, is not enough either.
    assert fl_server.put(f"/api/model/{theirs.id}/status/{ModelStatus.ERROR.value}").status_code == 403

    session.expire_all()
    assert session.get(Model, theirs.id).status == ModelStatus.RUNNING
    assert _job_statuses(session, theirs.id) == [JobStatus.IN_PROGRESS]
    assert _log_count(session, theirs.id) == 0

    assert _log(fl_server, mine.id, credential="mine") == 200
    assert _status(fl_server, mine.id, ModelStatus.ERROR, credential="mine") == 200


def test_job_dispatched_without_a_credential_cannot_report(fl_server, session, make_model):
    model = make_model(ModelStatus.RUNNING, [JobStatus.IN_PROGRESS])
    job = session.exec(select(FLJob).where(FLJob.model_id == model.id)).one()
    job.credential_hash = None
    session.commit()

    assert _status(fl_server, model.id, ModelStatus.ERROR) == 403
    assert fl_server.put(f"/api/model/{model.id}/status/{ModelStatus.ERROR.value}").status_code == 403


def test_running_job_reports_through_to_its_results(fl_server, session, make_model):
    model = make_model(ModelStatus.INITIATED, [JobStatus.IN_PROGRESS])
    job = session.exec(select(FLJob).where(FLJob.model_id == model.id)).one()
    net = FLNets(name=f"net-{uuid4()}", endpoint=f"http://fl-api-{uuid4()}:8000", fl_backend=FLBackend.NVFLARE)
    session.add(net)
    session.flush()
    scheduler = FLScheduler(net_id=net.id, status=NetStatus.BUSY, job_id=job.id)
    session.add(scheduler)
    session.commit()

    for step in (ModelStatus.PREPARED, ModelStatus.RUNNING):
        assert _status(fl_server, model.id, step) == 200
    assert _log(fl_server, model.id) == 200
    assert _status(fl_server, model.id, ModelStatus.RESULTS_UPLOADED) == 200

    session.expire_all()
    assert session.get(Model, model.id).status == ModelStatus.RESULTS_UPLOADED
    assert _job_statuses(session, model.id) == [JobStatus.COMPLETED]
    assert session.get(FLScheduler, scheduler.id).status == NetStatus.AVAILABLE
    # The run is over: nothing more is accepted for it.
    assert _log(fl_server, model.id) == 403
    assert _status(fl_server, model.id, ModelStatus.ERROR) == 403
    session.expire_all()
    assert session.get(Model, model.id).status == ModelStatus.RESULTS_UPLOADED


def test_run_that_reported_an_error_may_still_report_its_results(fl_server, session, make_model):
    """Flower's order when one client fails: ERROR mid-run, then the other clients, the later rounds, the results."""
    model = make_model(ModelStatus.RUNNING, [JobStatus.IN_PROGRESS])

    assert _log(fl_server, model.id) == 200
    assert _status(fl_server, model.id, ModelStatus.ERROR) == 200
    assert _job_statuses(session, model.id) == [JobStatus.COMPLETED]

    assert _log(fl_server, model.id) == 200
    assert _status(fl_server, model.id, ModelStatus.ERROR) == 200
    assert _status(fl_server, model.id, ModelStatus.RESULTS_UPLOADED) == 200

    session.expire_all()
    assert session.get(Model, model.id).status == ModelStatus.RESULTS_UPLOADED
    assert _log(fl_server, model.id) == 403


def test_failed_run_cannot_report_into_the_retry_queued_behind_it(fl_server, session, make_model):
    model = make_model(ModelStatus.ERROR, [JobStatus.COMPLETED])
    assert _log(fl_server, model.id) == 200

    session.add(FLJob(model_id=model.id, status=JobStatus.QUEUED))
    session.commit()

    assert _status(fl_server, model.id, ModelStatus.RESULTS_UPLOADED) == 403
    assert _job_statuses(session, model.id) == [JobStatus.COMPLETED, JobStatus.QUEUED]
