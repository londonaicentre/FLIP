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
from flip.fl_services.run_jobs import run_jobs


@pytest.fixture
def mock_db():
    with patch("flip.fl_services.run_jobs.get_session") as mock_get_session:
        mock_db = MagicMock()
        mock_get_session.return_value = mock_db
        yield mock_db


@pytest.fixture
def mock_check_for_available_net():
    with patch("flip.fl_services.run_jobs.check_for_available_net") as mock_check:
        scheduler = MagicMock(id="sched-id", netId="net-123")
        mock_check.return_value = scheduler
        yield mock_check


@pytest.fixture
def mock_check_for_queued_jobs():
    with patch("flip.fl_services.run_jobs.check_for_queued_jobs") as mock_check:
        job = IJobResponse(id=uuid4(), model_id=uuid4(), clients=["client1"])
        mock_check.return_value = job
        yield mock_check


@pytest.fixture
def model_id():
    return str(uuid4())


def test_run_jobs_success(mock_db, mock_check_for_available_net, mock_check_for_queued_jobs, caplog):
    with (
        patch("flip.fl_services.run_jobs.prepare_and_start_training") as mock_prepare,
    ):
        response = run_jobs(mock_db)

        scheduler = mock_check_for_available_net.return_value
        job = mock_check_for_queued_jobs.return_value
        assert response is None  # The function returns None on success
        assert "Training started successfully!" in caplog.text
        assert scheduler.netId in caplog.text
        assert str(job.id) in caplog.text
        assert str(job.model_id) in caplog.text
        mock_prepare.assert_called_once()


def test_run_jobs_no_available_net(mock_db, mock_check_for_available_net, caplog):
    mock_check_for_available_net.return_value = None
    response = run_jobs(mock_db)
    assert response is None
    assert "No available nets, will check again soon... 🔃" in caplog.text


def test_run_jobs_no_queued_job(mock_db, mock_check_for_available_net, mock_check_for_queued_jobs, caplog):
    mock_check_for_available_net.return_value = MagicMock(id="sched-id")
    mock_check_for_queued_jobs.return_value = None

    response = run_jobs(mock_db)
    assert response is None
    assert "No jobs waiting, will check again soon... 🔃" in caplog.text


def test_run_jobs_failure(mock_db, mock_check_for_available_net, mock_check_for_queued_jobs):
    with (
        patch("flip.fl_services.run_jobs.prepare_and_start_training", side_effect=Exception("start error")),
    ):
        with pytest.raises(HTTPException) as exc_info:
            run_jobs(mock_db)
        assert exc_info.value.status_code == 500
        assert "start error" in exc_info.value.detail
