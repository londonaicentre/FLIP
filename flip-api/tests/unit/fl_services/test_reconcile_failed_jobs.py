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

from flip_api.domain.schemas.status import FLJobStatus, ModelStatus
from flip_api.fl_services.reconcile_failed_jobs import (
    reconcile_failed_fl_jobs,
    reconcile_failed_fl_jobs_scheduled_task,
)

_ENDPOINT = "http://flip-fl-api-net-1:8000"
_BACKEND_JOB_ID = "11536428743664681318"


def _job_row(backend_job_id=_BACKEND_JOB_ID, endpoint=_ENDPOINT):
    """One row of the in-flight-jobs query: (job id, model id, backend job id, net endpoint)."""
    return (uuid4(), uuid4(), backend_job_id, endpoint)


def _mock_db(job_rows, model_statuses=None):
    """A session whose first exec() yields the job rows and whose later execs yield model statuses.

    ``model_statuses`` are consumed one per job that reaches the post-status re-read, in order.
    """
    jobs_result = MagicMock()
    jobs_result.all.return_value = job_rows

    results = [jobs_result]
    for model_status in model_statuses or []:
        status_result = MagicMock()
        status_result.one_or_none.return_value = model_status
        results.append(status_result)

    db = MagicMock()
    db.exec.side_effect = results
    return db


@pytest.fixture
def mock_dependencies():
    with (
        patch("flip_api.fl_services.reconcile_failed_jobs.get_backend_job_status") as mock_status,
        patch("flip_api.fl_services.reconcile_failed_jobs.fetch_run_logs") as mock_logs,
        patch("flip_api.fl_services.reconcile_failed_jobs.add_log") as mock_add_log,
        patch("flip_api.fl_services.reconcile_failed_jobs.update_model_status") as mock_update,
    ):
        mock_logs.return_value = None
        yield {
            "status": mock_status,
            "logs": mock_logs,
            "add_log": mock_add_log,
            "update_model_status": mock_update,
        }


def test_failed_run_errors_the_model_and_logs_the_cause(mock_dependencies):
    job = _job_row()
    db = _mock_db([job], model_statuses=[ModelStatus.RUNNING])
    mock_dependencies["status"].return_value = FLJobStatus.FAILED
    mock_dependencies["logs"].return_value = "ImportError: cannot import name 'min_clients_from_run_config'"

    reported = reconcile_failed_fl_jobs(db)

    assert reported == 1
    mock_dependencies["add_log"].assert_called_once()
    log_args, log_kwargs = mock_dependencies["add_log"].call_args
    assert log_args[0] == job[1]
    assert _BACKEND_JOB_ID in log_args[1]
    assert "min_clients_from_run_config" in log_args[1]
    assert log_kwargs["success"] is False
    mock_dependencies["update_model_status"].assert_called_once_with(job[1], ModelStatus.ERROR, db)


def test_failure_without_retrievable_logs_names_the_manual_fallback(mock_dependencies):
    db = _mock_db([_job_row()], model_statuses=[ModelStatus.INITIATED])
    mock_dependencies["status"].return_value = FLJobStatus.FAILED
    mock_dependencies["logs"].return_value = None

    reconcile_failed_fl_jobs(db)

    message = mock_dependencies["add_log"].call_args[0][1]
    assert "flwr log" in message


@pytest.mark.parametrize(
    "backend_status",
    [FLJobStatus.RUNNING, FLJobStatus.PENDING, FLJobStatus.FINISHED, FLJobStatus.STOPPED, None],
)
def test_non_failed_runs_are_left_alone(mock_dependencies, backend_status):
    # FINISHED in particular: a run whose ServerApp has finished is routinely still
    # uploading results, and the hub's own RESULTS_UPLOADED callback owns that transition.
    db = _mock_db([_job_row()])
    mock_dependencies["status"].return_value = backend_status

    reported = reconcile_failed_fl_jobs(db)

    assert reported == 0
    mock_dependencies["add_log"].assert_not_called()
    mock_dependencies["update_model_status"].assert_not_called()
    mock_dependencies["logs"].assert_not_called()


def test_no_in_flight_jobs_does_not_call_the_fl_api(mock_dependencies):
    db = _mock_db([])

    assert reconcile_failed_fl_jobs(db) == 0
    mock_dependencies["status"].assert_not_called()


@pytest.mark.parametrize(
    "settled_status",
    [ModelStatus.RESULTS_UPLOADED, ModelStatus.STOPPED, ModelStatus.ERROR],
)
def test_model_that_settled_during_the_status_call_wins_the_race(mock_dependencies, settled_status):
    db = _mock_db([_job_row()], model_statuses=[settled_status])
    mock_dependencies["status"].return_value = FLJobStatus.FAILED

    reported = reconcile_failed_fl_jobs(db)

    assert reported == 0
    mock_dependencies["add_log"].assert_not_called()
    mock_dependencies["update_model_status"].assert_not_called()


def test_one_unreachable_net_does_not_stop_the_other_jobs(mock_dependencies):
    unreachable, healthy = _job_row(), _job_row(backend_job_id="222")
    db = _mock_db([unreachable, healthy], model_statuses=[ModelStatus.RUNNING])
    mock_dependencies["status"].side_effect = [ConnectionError("net-1 is down"), FLJobStatus.FAILED]

    reported = reconcile_failed_fl_jobs(db)

    assert reported == 1
    db.rollback.assert_called_once()
    mock_dependencies["update_model_status"].assert_called_once_with(healthy[1], ModelStatus.ERROR, db)


def test_scheduled_task_never_raises():
    with patch("flip_api.fl_services.reconcile_failed_jobs.get_engine", side_effect=RuntimeError("no db")):
        reconcile_failed_fl_jobs_scheduled_task()
