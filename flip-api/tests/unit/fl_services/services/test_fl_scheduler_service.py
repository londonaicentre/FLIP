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

from flip_api.db.models.main_models import FLJob
from flip_api.domain.interfaces.fl import (
    IJobResponse,
    INetDetails,
    IRequiredTrainingInformation,
    ISchedulerResponse,
)
from flip_api.domain.schemas.status import JobStatus, ModelStatus, NetStatus
from flip_api.domain.schemas.types import FLBackend, FLLogEvent
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
    with (
        patch(
            "flip_api.fl_services.services.fl_scheduler_service.bundle_nvflare_application",
            return_value="s3://dest/model",
        ) as mock_bundle,
        patch("flip_api.fl_services.services.fl_scheduler_service.get_net_by_model_id") as mock_get_net,
        patch(
            "flip_api.fl_services.services.fl_scheduler_service.validate_client_availability"
        ) as mock_validate_clients,
        patch("flip_api.fl_services.services.fl_scheduler_service.get_bundle_urls", return_value=["url1", "url2"]),
        patch("flip_api.fl_services.services.fl_scheduler_service.start_training") as mock_start,
        patch("flip_api.fl_services.services.fl_scheduler_service.add_log") as mock_log,
    ):
        # The net self-reports its backend, so resolve_backend(session, net) returns it
        # without touching the DB or any boot-time env var.
        mock_get_net.return_value = INetDetails(endpoint="endpoint", name="net-name", fl_backend=FLBackend.NVFLARE)

        fl_scheduler_service.prepare_and_start_training(
            model_id=model_id,
            fl_job_id=fl_job_id,
            trust_ids=[uuid4()],
            session=fake_session,
        )

        mock_bundle.assert_called_once_with(model_id)
        mock_validate_clients.assert_called_once()
        mock_start.assert_called_once()
        mock_log.assert_called()


def test_prepare_and_start_training_failure(fake_session, model_id, fl_job_id):
    with (
        patch(
            "flip_api.fl_services.services.fl_scheduler_service.bundle_nvflare_application",
            side_effect=Exception("bundle failed"),
        ),
        patch("flip_api.fl_services.services.fl_scheduler_service.get_net_by_model_id") as mock_get_net,
        patch("flip_api.fl_services.services.fl_scheduler_service.remove_job") as mock_remove,
        patch("flip_api.fl_services.services.fl_scheduler_service.add_log") as mock_log,
        patch("flip_api.fl_services.services.fl_scheduler_service.update_model_status") as mock_status,
    ):
        # Net reports nvflare so the nvflare bundler (patched to raise) is the path taken.
        mock_get_net.return_value = INetDetails(endpoint="endpoint", name="net-name", fl_backend=FLBackend.NVFLARE)
        # The original exception propagates; only the user-visible FL log is genericised.
        with pytest.raises(Exception, match="bundle failed"):
            fl_scheduler_service.prepare_and_start_training(
                model_id=model_id,
                fl_job_id=fl_job_id,
                trust_ids=[uuid4()],
                session=fake_session,
            )

        mock_remove.assert_called_once_with(fl_job_id, fake_session)
        mock_status.assert_called_once_with(model_id, ModelStatus.ERROR, fake_session)
        mock_log.assert_called_once_with(model_id, "Failed to start training", fake_session, success=False)


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

    # Patched so its batch commit doesn't skew the count asserted below;
    # the re-emission behaviour has its own tests in TestLogQueuePositions.
    with patch.object(fl_scheduler_service, "log_queue_positions"):
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
    fake_session.exec.return_value.first.return_value = ("endpoint", "name", FLBackend.NVFLARE)

    result = fl_scheduler_service.get_net_by_model_id(model_id, fake_session)

    assert isinstance(result, INetDetails)
    assert result.endpoint == "endpoint"
    assert result.name == "name"
    assert result.fl_backend == FLBackend.NVFLARE


def test_get_net_by_model_id_not_found(fake_session, model_id):
    fake_session.exec.return_value.first.return_value = None
    with pytest.raises(NotFoundError, match=f"Net not found for model ID: {model_id}"):
        fl_scheduler_service.get_net_by_model_id(model_id, fake_session)


def test_get_net_by_name(fake_session):
    fake_session.exec.return_value.first.return_value = ("endpoint", "net-name", FLBackend.FLOWER)

    result = fl_scheduler_service.get_net_by_name("net-name", fake_session)

    assert isinstance(result, INetDetails)
    assert result.name == "net-name"
    assert result.fl_backend == FLBackend.FLOWER


def test_get_net_by_name_not_found(fake_session):
    fake_session.exec.return_value.first.return_value = None

    result = fl_scheduler_service.get_net_by_name("net-missing", fake_session)
    assert result is None


def test_get_nets(fake_session):
    fake_session.exec.return_value.all.return_value = [
        ("endpoint", "net1", FLBackend.NVFLARE),
        ("endpoint2", "net2", FLBackend.FLOWER),
    ]

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
    trust_id = uuid4()
    trust = MagicMock(id=trust_id)
    job = MagicMock()
    job.id = uuid4()
    job.model_id = model_id
    job.trusts = [trust]

    scheduler = MagicMock()

    fake_session.exec.side_effect = [
        MagicMock(first=MagicMock(return_value=job)),
    ]
    fake_session.get.return_value = scheduler

    with patch("flip_api.fl_services.services.fl_scheduler_service.validate_trust_ids", return_value=True) as v:
        result = fl_scheduler_service.check_for_queued_jobs(scheduler_id, fake_session)

    assert isinstance(result, IJobResponse)
    assert result.model_id == job.model_id
    assert result.trust_ids == [trust_id]
    v.assert_called_once_with(model_id, [trust_id], fake_session)
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


# get_slot_names_by_trust_ids — added in the connection-status PR.

def test_get_slot_names_by_trust_ids_empty_input_returns_empty(fake_session):
    """An empty input is a "no trusts to resolve" sentinel — return {} without
    issuing a DB query.
    """
    result = fl_scheduler_service.get_slot_names_by_trust_ids([], fake_session)

    assert result == {}
    fake_session.exec.assert_not_called()


def test_get_slot_names_by_trust_ids_maps_assigned_slots(fake_session):
    """Rows from FLKitSlot are folded into a {trust_id: slot_name} map; rows
    whose trust_id is NULL are filtered out (slot exists but is unassigned).
    """
    trust_a = uuid4()
    trust_b = uuid4()
    fake_session.exec.return_value.all.return_value = [
        (trust_a, "Trust_1"),
        (None, "Trust_unassigned"),
        (trust_b, "Trust_2"),
    ]

    result = fl_scheduler_service.get_slot_names_by_trust_ids([trust_a, trust_b], fake_session)

    assert result == {trust_a: "Trust_1", trust_b: "Trust_2"}


def _queued_job(model_id=None):
    return FLJob(id=uuid4(), model_id=model_id or uuid4())


def _position_details(job_id, position):
    """One details payload, as the DISTINCT ON dedup query returns them (JSONB dicts, not rows)."""
    return {"position": position, "job_id": str(job_id)}


def _exec_returning(*result_lists):
    """One MagicMock per session.exec(...) call, whose .all() yields the given list."""
    return [MagicMock(all=MagicMock(return_value=list(results))) for results in result_lists]


class TestLogQueuePositions:
    """log_queue_positions writes one typed row per queued job whose position changed.

    Emit-on-change keyed by job id makes the helper idempotent, so every queue
    mutation site can call it unconditionally without spamming the feed.
    """

    def test_empty_queue_emits_nothing(self, fake_session):
        fake_session.exec.side_effect = _exec_returning([])
        with patch.object(fl_scheduler_service, "add_log") as mock_add_log:
            fl_scheduler_service.log_queue_positions(fake_session)
        mock_add_log.assert_not_called()
        fake_session.commit.assert_not_called()

    def test_new_job_gets_its_position_logged(self, fake_session):
        job = _queued_job()
        fake_session.exec.side_effect = _exec_returning([job], [])
        with patch.object(fl_scheduler_service, "add_log") as mock_add_log:
            fl_scheduler_service.log_queue_positions(fake_session)
        mock_add_log.assert_called_once_with(
            job.model_id,
            None,
            fake_session,
            event_type=FLLogEvent.QUEUE_POSITION.value,
            details={"position": 1, "job_id": str(job.id)},
            transaction=fake_session,
        )
        # The rows are batched: one commit for the whole emission, not one per row.
        fake_session.commit.assert_called_once()

    def test_unchanged_position_is_not_relogged(self, fake_session):
        job = _queued_job()
        fake_session.exec.side_effect = _exec_returning([job], [_position_details(job.id, 1)])
        with patch.object(fl_scheduler_service, "add_log") as mock_add_log:
            fl_scheduler_service.log_queue_positions(fake_session)
        mock_add_log.assert_not_called()
        fake_session.commit.assert_not_called()

    def test_moved_job_logs_its_new_position(self, fake_session):
        job = _queued_job()
        fake_session.exec.side_effect = _exec_returning([job], [_position_details(job.id, 2)])
        with patch.object(fl_scheduler_service, "add_log") as mock_add_log:
            fl_scheduler_service.log_queue_positions(fake_session)
        mock_add_log.assert_called_once_with(
            job.model_id,
            None,
            fake_session,
            event_type=FLLogEvent.QUEUE_POSITION.value,
            details={"position": 1, "job_id": str(job.id)},
            transaction=fake_session,
        )

    def test_second_of_two_queued_jobs_gets_position_two(self, fake_session):
        first, second = _queued_job(), _queued_job()
        fake_session.exec.side_effect = _exec_returning(
            [first, second], [_position_details(first.id, 1)]
        )
        with patch.object(fl_scheduler_service, "add_log") as mock_add_log:
            fl_scheduler_service.log_queue_positions(fake_session)
        mock_add_log.assert_called_once_with(
            second.model_id,
            None,
            fake_session,
            event_type=FLLogEvent.QUEUE_POSITION.value,
            details={"position": 2, "job_id": str(second.id)},
            transaction=fake_session,
        )

    def test_two_queued_jobs_of_the_same_model_do_not_oscillate(self, fake_session):
        # FLJob has no uniqueness on model_id, so a double initiate can queue the
        # same model twice. The last-row lookup is keyed by job id, so each job
        # compares against its own latest row — a model-keyed lookup would let the
        # newest row suppress one job and re-emit the other forever (oscillation).
        model_id = uuid4()
        job_a, job_b = _queued_job(model_id), _queued_job(model_id)
        newer = _position_details(job_b.id, 2)
        older = _position_details(job_a.id, 1)
        fake_session.exec.side_effect = _exec_returning([job_a, job_b], [newer, older])
        with patch.object(fl_scheduler_service, "add_log") as mock_add_log:
            fl_scheduler_service.log_queue_positions(fake_session)
        mock_add_log.assert_not_called()
        fake_session.commit.assert_not_called()

    def test_reinitiated_model_relogs_even_at_the_same_position(self, fake_session):
        # Same model, new job: the prior row belongs to the model's previous run,
        # so its matching position must not suppress the new run's first row.
        job = _queued_job()
        fake_session.exec.side_effect = _exec_returning([job], [_position_details(uuid4(), 1)])
        with patch.object(fl_scheduler_service, "add_log") as mock_add_log:
            fl_scheduler_service.log_queue_positions(fake_session)
        mock_add_log.assert_called_once()

    def test_only_the_latest_prior_row_counts(self, fake_session):
        # DISTINCT ON should hand back one row per job, but the Python fold keeps
        # first-seen (newest, per the query's ordering) as defense in depth: an
        # older matching row behind a newer different one must not suppress emission.
        job = _queued_job()
        newer = _position_details(job.id, 2)
        older = _position_details(job.id, 1)
        fake_session.exec.side_effect = _exec_returning([job], [newer, older])
        with patch.object(fl_scheduler_service, "add_log") as mock_add_log:
            fl_scheduler_service.log_queue_positions(fake_session)
        mock_add_log.assert_called_once()

    def test_batched_rows_land_in_a_single_commit(self, fake_session):
        # add_log deliberately unpatched: transaction= must make it defer its
        # per-row commit so the whole emission lands in the one batch commit.
        first, second = _queued_job(), _queued_job()
        fake_session.exec.side_effect = _exec_returning([first, second], [])
        fl_scheduler_service.log_queue_positions(fake_session)
        assert fake_session.add.call_count == 2
        fake_session.commit.assert_called_once()

    def test_emission_failure_never_raises_and_rolls_back(self, fake_session):
        job = _queued_job()
        fake_session.exec.side_effect = _exec_returning([job], [])
        with patch.object(fl_scheduler_service, "add_log", side_effect=Exception("db down")):
            fl_scheduler_service.log_queue_positions(fake_session)  # must not raise
        fake_session.rollback.assert_called_once()
        fake_session.commit.assert_not_called()
        fake_session.invalidate.assert_not_called()

    def test_emission_failure_with_failing_rollback_still_never_raises(self, fake_session):
        job = _queued_job()
        fake_session.exec.side_effect = _exec_returning([job], [])
        fake_session.rollback.side_effect = Exception("rollback failed")
        with patch.object(fl_scheduler_service, "add_log", side_effect=Exception("db down")):
            fl_scheduler_service.log_queue_positions(fake_session)  # must not raise
        # A failed rollback leaves the session raising PendingRollbackError on
        # every subsequent use; invalidate() is what hands the caller back a
        # session that works on a fresh connection.
        fake_session.invalidate.assert_called_once()

    def test_update_fl_scheduler_reemits_queue_positions(self, fake_session, model_id, fl_job_id):
        """Completing a still-QUEUED job (stopped/errored queued model) re-ranks the tail."""
        job = MagicMock()
        job.id = fl_job_id
        fake_session.exec.side_effect = [
            MagicMock(first=MagicMock(return_value=job)),
            MagicMock(first=MagicMock(return_value=MagicMock())),
        ]
        with patch.object(fl_scheduler_service, "log_queue_positions") as mock_positions:
            fl_scheduler_service.update_fl_scheduler(model_id, fake_session)
        mock_positions.assert_called_once_with(fake_session)

    def test_update_fl_scheduler_without_a_job_does_not_reemit(self, fake_session, model_id):
        fake_session.exec.return_value.first.return_value = None
        with patch.object(fl_scheduler_service, "log_queue_positions") as mock_positions:
            fl_scheduler_service.update_fl_scheduler(model_id, fake_session)
        mock_positions.assert_not_called()

    def test_remove_job_from_queue_reemits_queue_positions(self, fake_session, model_id):
        """Deleting a queued job (stop/abort of a queued model) re-ranks everything behind it."""
        fake_session.exec.return_value.all.return_value = [MagicMock()]
        with patch.object(fl_scheduler_service, "log_queue_positions") as mock_positions:
            fl_scheduler_service.remove_job_from_queue(model_id, fake_session)
        mock_positions.assert_called_once_with(fake_session)

    def test_remove_job_from_queue_without_jobs_does_not_reemit(self, fake_session, model_id):
        fake_session.exec.return_value.all.return_value = []
        with patch.object(fl_scheduler_service, "log_queue_positions") as mock_positions:
            fl_scheduler_service.remove_job_from_queue(model_id, fake_session)
        mock_positions.assert_not_called()
