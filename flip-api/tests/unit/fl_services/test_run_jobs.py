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
from fastapi import HTTPException

from flip_api.domain.interfaces.fl import IJobResponse
from flip_api.fl_services.run_jobs import _recover_stale_busy_schedulers, run_jobs_core


@pytest.fixture
def mock_db():
    # run_jobs_core takes the session as an argument (the HTTP entrypoint that
    # used Depends(get_session) was removed), so the test just hands it a mock.
    return MagicMock()


@pytest.fixture(autouse=True)
def deployment_mode_disabled():
    # With a MagicMock session the real is_deployment_mode_enabled would read a
    # truthy row and report the gate as closed, so default it to open here; the
    # deployment-mode tests below override the return value.
    with patch("flip_api.fl_services.run_jobs.is_deployment_mode_enabled", return_value=False) as mock_enabled:
        yield mock_enabled


@pytest.fixture
def mock_check_for_available_net():
    with patch("flip_api.fl_services.run_jobs.check_for_available_net") as mock_check:
        scheduler = MagicMock(id="sched-id", netId="net-123")
        mock_check.return_value = scheduler
        yield mock_check


@pytest.fixture
def mock_check_for_queued_jobs():
    with patch("flip_api.fl_services.run_jobs.check_for_queued_jobs") as mock_check:
        job = IJobResponse(id=uuid4(), model_id=uuid4(), trust_ids=[uuid4()])
        mock_check.return_value = job
        yield mock_check


@pytest.fixture
def model_id():
    return str(uuid4())


def test_run_jobs_success(mock_db, mock_check_for_available_net, mock_check_for_queued_jobs, caplog):
    with (
        patch("flip_api.fl_services.run_jobs.prepare_and_start_training", return_value=True) as mock_prepare,
    ):
        response = run_jobs_core(mock_db)

        scheduler = mock_check_for_available_net.return_value
        job = mock_check_for_queued_jobs.return_value
        assert response is None  # The function returns None on success
        assert "Training started successfully!" in caplog.text
        assert scheduler.netId in caplog.text
        assert str(job.id) in caplog.text
        assert str(job.model_id) in caplog.text
        mock_prepare.assert_called_once()


def test_run_jobs_aborted_before_submission(mock_db, mock_check_for_available_net, mock_check_for_queued_jobs, caplog):
    # prepare_and_start_training returning False means the job was aborted mid-prepare (#787):
    # the tick must not claim training started.
    with (
        patch("flip_api.fl_services.run_jobs.prepare_and_start_training", return_value=False),
    ):
        response = run_jobs_core(mock_db)

        assert response is None
        assert "Training started successfully!" not in caplog.text
        assert "aborted before submission" in caplog.text


def test_run_jobs_no_available_net(mock_db, mock_check_for_available_net, caplog):
    mock_check_for_available_net.return_value = None
    response = run_jobs_core(mock_db)
    assert response is None
    assert "No available nets, will check again soon... 🔃" in caplog.text


def test_run_jobs_no_queued_job(mock_db, mock_check_for_available_net, mock_check_for_queued_jobs, caplog):
    mock_check_for_available_net.return_value = MagicMock(id="sched-id")
    mock_check_for_queued_jobs.return_value = None

    response = run_jobs_core(mock_db)
    assert response is None
    assert "No jobs waiting, will check again soon... 🔃" in caplog.text


def test_run_jobs_failure(mock_db, mock_check_for_available_net, mock_check_for_queued_jobs):
    with (
        patch("flip_api.fl_services.run_jobs.prepare_and_start_training", side_effect=Exception("start error")),
    ):
        with pytest.raises(HTTPException) as exc_info:
            run_jobs_core(mock_db)
        assert exc_info.value.status_code == 500
        assert "start error" in exc_info.value.detail


# ── deployment-mode gate tests ──────────────────────────────────────────────


def test_run_jobs_deployment_mode_pauses_job_pickup(
    mock_db, deployment_mode_disabled, mock_check_for_available_net, mock_check_for_queued_jobs, caplog
):
    """Deployment mode enabled → no net lookup, no job pickup, no training start."""
    deployment_mode_disabled.return_value = True

    with patch("flip_api.fl_services.run_jobs.prepare_and_start_training") as mock_prepare:
        response = run_jobs_core(mock_db)

    assert response is None
    assert "Deployment mode enabled" in caplog.text
    mock_check_for_available_net.assert_not_called()
    mock_check_for_queued_jobs.assert_not_called()
    mock_prepare.assert_not_called()


def test_run_jobs_deployment_mode_still_recovers_stale_schedulers(mock_db, deployment_mode_disabled):
    """Stale-BUSY scheduler recovery must keep running while the gate is closed."""
    deployment_mode_disabled.return_value = True
    mock_db.execute.return_value.rowcount = 0

    run_jobs_core(mock_db)

    mock_db.execute.assert_called_once()


# ── _recover_stale_busy_schedulers tests ────────────────────────────────────


def _make_mock_session(rowcount: int) -> MagicMock:
    """Create a mocked Session whose db.execute() returns a result with the given rowcount."""
    session = MagicMock(name="mock_session")
    mock_result = MagicMock(name="mock_result")
    mock_result.rowcount = rowcount
    session.execute.return_value = mock_result
    return session


def test_recover_stale_busy_no_busy_rows(caplog):
    """No BUSY rows → 0 returned, no db.commit() called."""
    session = _make_mock_session(rowcount=0)

    result = _recover_stale_busy_schedulers(session)

    assert result == 0
    session.commit.assert_not_called()


def test_recover_stale_busy_job_id_none(caplog):
    """BUSY + job_id=None → reset, commit called, returns 1."""
    session = _make_mock_session(rowcount=1)

    result = _recover_stale_busy_schedulers(session)

    assert result == 1
    session.commit.assert_called_once()
    assert "Recovered 1 stale BUSY scheduler(s)" in caplog.text


def test_recover_stale_busy_valid_job(caplog):
    """BUSY + valid job exists → 0 returned (subquery finds the job), no commit."""
    session = _make_mock_session(rowcount=0)

    result = _recover_stale_busy_schedulers(session)

    assert result == 0
    session.commit.assert_not_called()


def test_recover_stale_busy_deleted_job(caplog):
    """BUSY + deleted job → reset, commit called, returns 2."""
    session = _make_mock_session(rowcount=2)

    result = _recover_stale_busy_schedulers(session)

    assert result == 2
    session.commit.assert_called_once()
    assert "Recovered 2 stale BUSY scheduler(s)" in caplog.text


# ── queue-position re-emission ───────────────────────────────────────────────


def test_run_jobs_reemits_queue_positions_after_pickup(
    mock_db, mock_check_for_available_net, mock_check_for_queued_jobs
):
    """A pickup advances every remaining queued model, so positions are re-logged."""
    with (
        patch("flip_api.fl_services.run_jobs.prepare_and_start_training"),
        patch("flip_api.fl_services.run_jobs.log_queue_positions") as mock_positions,
    ):
        run_jobs_core(mock_db)

    mock_positions.assert_called_once_with(mock_db)


def test_run_jobs_does_not_reemit_when_no_job_was_picked(
    mock_db, mock_check_for_available_net, mock_check_for_queued_jobs
):
    mock_check_for_queued_jobs.return_value = None

    with patch("flip_api.fl_services.run_jobs.log_queue_positions") as mock_positions:
        run_jobs_core(mock_db)

    mock_positions.assert_not_called()
