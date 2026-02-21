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

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from flip_api.domain.interfaces.fl import (
    IJobResponse,
    INetDetails,
    IRequiredTrainingInformation,
    ISchedulerResponse,
)
from flip_api.domain.schemas.status import JobStatus, ModelStatus, NetStatus
from flip_api.fl_services.services import fl_scheduler_service
from flip_api.utils.exceptions import NotFoundError


@pytest.fixture
def fake_session():
    return MagicMock()


@pytest.fixture
def model_id():
    return uuid4()


@pytest.fixture
def fl_job_id():
    return uuid4()


@pytest.fixture
def scheduler_id():
    return uuid4()


def test_prepare_and_start_training_success(fake_session, model_id, fl_job_id):
    from flip_api.domain.interfaces.fl import JobTypes

    with (
        patch(
            "flip_api.fl_services.services.fl_scheduler_service.bundle_application",
            return_value=(2, JobTypes.standard),
        ) as mock_bundle,
        patch("flip_api.fl_services.services.fl_scheduler_service.get_net_by_model_id") as mock_get_net,
        patch(
            "flip_api.fl_services.services.fl_scheduler_service.validate_client_availability"
        ) as mock_validate_clients,
        patch("flip_api.fl_services.services.fl_scheduler_service.get_bundle_urls", return_value=["url1", "url2"]),
        patch("flip_api.fl_services.services.fl_scheduler_service.start_training") as mock_start,
        patch("flip_api.fl_services.services.fl_scheduler_service.add_log") as mock_log,
    ):
        mock_get_net.return_value = INetDetails(endpoint="endpoint", name="net-name")

        fl_scheduler_service.prepare_and_start_training(
            model_id=model_id,
            fl_job_id=fl_job_id,
            clients=["client1"],
            request_id="req-id",
            session=fake_session,
        )

        mock_bundle.assert_called_once_with(model_id)
        mock_validate_clients.assert_called_once()
        mock_start.assert_called_once()
        mock_log.assert_called()


def test_prepare_and_start_training_failure(fake_session, model_id, fl_job_id):
    with (
        patch(
            "flip_api.fl_services.services.fl_scheduler_service.bundle_application",
            side_effect=Exception("bundle failed"),
        ),
        patch("flip_api.fl_services.services.fl_scheduler_service.remove_job") as mock_remove,
        patch("flip_api.fl_services.services.fl_scheduler_service.add_log") as mock_log,
        patch("flip_api.fl_services.services.fl_scheduler_service.update_model_status") as mock_status,
    ):
        with pytest.raises(Exception, match="bundle failed"):
            fl_scheduler_service.prepare_and_start_training(
                model_id=model_id,
                fl_job_id=fl_job_id,
                clients=["client1"],
                request_id="req-id",
                session=fake_session,
            )

        mock_remove.assert_called_once_with(fl_job_id, fake_session)
        mock_status.assert_called_once_with(model_id, ModelStatus.ERROR, fake_session)
        mock_log.assert_called()


def test_update_fl_scheduler_success(fake_session, model_id, fl_job_id):
    job = MagicMock()
    job.id = fl_job_id
    scheduler = MagicMock()

    fake_session.exec.side_effect = [
        MagicMock(first=MagicMock(return_value=job)),
        MagicMock(first=MagicMock(return_value=scheduler)),
    ]

    fl_scheduler_service.update_fl_scheduler(model_id, fake_session)

    assert job.status == JobStatus.COMPLETED
    assert job.completed is not None
    assert scheduler.status == NetStatus.AVAILABLE
    fake_session.commit.assert_called_once()


def test_update_fl_scheduler_no_job(fake_session, model_id):
    fake_session.exec.return_value.first.return_value = None
    fl_scheduler_service.update_fl_scheduler(model_id, fake_session)
    fake_session.commit.assert_called_once()


def test_remove_job_success(fake_session, fl_job_id):
    job = MagicMock()
    fake_session.get.return_value = job

    fl_scheduler_service.remove_job(fl_job_id, fake_session)

    assert job.status == JobStatus.DELETED
    assert job.started is None
    fake_session.commit.assert_called_once()


def test_remove_job_not_found(fake_session):
    fake_session.get.return_value = None
    missing_job_id = "missing-id"
    with pytest.raises(NotFoundError, match=f"FLJob with id {missing_job_id} not found"):
        fl_scheduler_service.remove_job(missing_job_id, fake_session)


def test_remove_job_from_queue(fake_session, model_id):
    job = MagicMock()
    fake_session.exec.return_value.all.return_value = [job]

    fl_scheduler_service.remove_job_from_queue(model_id, fake_session)

    assert job.status == JobStatus.DELETED
    assert job.completed is not None
    fake_session.commit.assert_called_once()


def test_remove_job_from_queue_no_jobs(fake_session, model_id):
    fake_session.exec.return_value.all.return_value = []

    fl_scheduler_service.remove_job_from_queue(model_id, fake_session)
    fake_session.commit.assert_not_called()


def test_revert_scheduler_pickup(fake_session, scheduler_id):
    scheduler = MagicMock()
    fake_session.get.return_value = scheduler

    fl_scheduler_service.revert_scheduler_pickup(scheduler_id, fake_session)

    assert scheduler.status == NetStatus.AVAILABLE
    assert scheduler.job_id is None
    fake_session.commit.assert_called_once()


def test_revert_scheduler_pickup_not_found(fake_session):
    fake_session.get.return_value = None
    missing_scheduler_id = "missing-id"
    with pytest.raises(NotFoundError, match=f"FLScheduler with id {missing_scheduler_id} not found"):
        fl_scheduler_service.revert_scheduler_pickup(missing_scheduler_id, fake_session)


def test_get_net_by_model_id(fake_session, model_id):
    fake_session.exec.return_value.first.return_value = ("endpoint", "name")

    result = fl_scheduler_service.get_net_by_model_id(model_id, fake_session)

    assert isinstance(result, INetDetails)
    assert result.endpoint == "endpoint"
    assert result.name == "name"


def test_get_net_by_model_id_not_found(fake_session, model_id):
    fake_session.exec.return_value.first.return_value = None
    with pytest.raises(NotFoundError, match=f"Net not found for model ID: {model_id}"):
        fl_scheduler_service.get_net_by_model_id(model_id, fake_session)


def test_get_net_by_name(fake_session):
    fake_session.exec.return_value.first.return_value = ("endpoint", "net-name")

    result = fl_scheduler_service.get_net_by_name("net-name", fake_session)

    assert isinstance(result, INetDetails)
    assert result.name == "net-name"


def test_get_net_by_name_not_found(fake_session):
    fake_session.exec.return_value.first.return_value = None

    result = fl_scheduler_service.get_net_by_name("net-missing", fake_session)
    assert result is None


def test_get_nets(fake_session):
    fake_session.exec.return_value.all.return_value = [("endpoint", "net1"), ("endpoint2", "net2")]

    results = fl_scheduler_service.get_nets(fake_session)

    assert len(results) == 2
    assert all(isinstance(net, INetDetails) for net in results)


def test_get_nets_no_results(fake_session):
    fake_session.exec.return_value.all.return_value = []
    with pytest.raises(Exception, match="No db response returned when querying for nets"):
        fl_scheduler_service.get_nets(fake_session)


def test_check_for_available_net(fake_session):
    scheduler = MagicMock()
    scheduler.id = uuid4()
    scheduler.net_id = uuid4()
    scheduler.status = NetStatus.BUSY

    fake_session.exec.return_value.first.return_value = scheduler

    result = fl_scheduler_service.check_for_available_net(fake_session)

    assert isinstance(result, ISchedulerResponse)
    assert result.id == scheduler.id
    assert result.netId == scheduler.net_id
    fake_session.commit.assert_called_once()


def test_check_for_available_net_none(fake_session):
    fake_session.exec.return_value.first.return_value = None
    result = fl_scheduler_service.check_for_available_net(fake_session)
    assert result is None


def test_check_for_queued_jobs_success(fake_session, scheduler_id, model_id):
    job = MagicMock()
    job.id = uuid4()
    job.model_id = model_id
    job.clients = ["client1"]

    scheduler = MagicMock()

    fake_session.exec.side_effect = [
        MagicMock(first=MagicMock(return_value=job)),
        MagicMock(first=MagicMock(return_value=True)),  # validate_trusts returns True
    ]
    fake_session.get.return_value = scheduler

    with patch("flip_api.fl_services.services.fl_scheduler_service.validate_trusts", return_value=True):
        result = fl_scheduler_service.check_for_queued_jobs(scheduler_id, fake_session)

    assert isinstance(result, IJobResponse)
    assert result.model_id == job.model_id
    fake_session.commit.assert_called()


def test_check_for_queued_jobs_none(fake_session, scheduler_id):
    fake_session.exec.return_value.first.return_value = None

    with patch("flip_api.fl_services.services.fl_scheduler_service.revert_scheduler_pickup") as mock_revert:
        result = fl_scheduler_service.check_for_queued_jobs(scheduler_id, fake_session)
        assert result is None
        mock_revert.assert_called_once()


def test_get_required_training_details(fake_session, model_id):
    model = MagicMock()
    model.project_id = uuid4()

    latest_query = MagicMock()
    latest_query.query = "SELECT * FROM patients"

    fake_session.exec.side_effect = [
        MagicMock(first=MagicMock(return_value=model)),
        MagicMock(first=MagicMock(return_value=latest_query)),
    ]

    result = fl_scheduler_service.get_required_training_details(model_id, fake_session)

    assert isinstance(result, IRequiredTrainingInformation)
    assert result.project_id == str(model.project_id)
    assert result.cohort_query == latest_query.query


def test_get_required_training_details_no_model(fake_session, model_id):
    fake_session.exec.return_value.first.return_value = None

    with pytest.raises(NotFoundError, match=f"Model with ID {model_id} not found"):
        fl_scheduler_service.get_required_training_details(model_id, fake_session)


def test_get_required_training_details_no_query(fake_session, model_id):
    model = MagicMock()
    model.project_id = uuid4()

    fake_session.exec.side_effect = [
        MagicMock(first=MagicMock(return_value=model)),
        MagicMock(first=MagicMock(return_value=None)),
    ]

    with pytest.raises(NotFoundError, match="No cohort query found for this project"):
        fl_scheduler_service.get_required_training_details(model_id, fake_session)
