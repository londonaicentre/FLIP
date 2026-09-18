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

"""Decision logic of the FL job reconcile, with every DB read behind a patched seam.

The sweep's own DB access (`_load_in_flight_jobs`, `_reread_statuses`, `_is_models_newest_job`,
`_release_job`) is patched by name here, so a test states the world in terms of statuses
rather than of a positional `exec()` mock sequence. The SQL those seams run is exercised
against a real Postgres in tests/integration/test_fl_reconcile_db_flow.py.
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from uuid import uuid4

import httpx
import pytest

from flip_api.domain.interfaces.fl import IJobMetaData
from flip_api.domain.schemas.status import FLJobStatus, JobStatus, ModelStatus
from flip_api.domain.schemas.types import FLBackend
from flip_api.fl_services.reconcile_failed_jobs import (
    InFlightJob,
    reconcile_failed_fl_jobs,
    reconcile_failed_fl_jobs_scheduled_task,
)
from flip_api.fl_services.services.fl_service import RunLogTail

_ENDPOINT = "http://fl-api-net-1:8000"
_BACKEND_JOB_ID = "11536428743664681318"
_GRACE_MINUTES = 30
_MODULE = "flip_api.fl_services.reconcile_failed_jobs"


def _job(
    backend_job_id=_BACKEND_JOB_ID,
    started=None,
    endpoint=_ENDPOINT,
    fl_backend=FLBackend.FLOWER,
    model_status=ModelStatus.RUNNING,
) -> InFlightJob:
    """One in-flight job as the sweep loads it. ``started`` defaults to just-now (inside the grace)."""
    if started is None:
        started = datetime.utcnow()
    return InFlightJob(uuid4(), uuid4(), backend_job_id, started, endpoint, fl_backend, model_status)


def _meta(status, details=None):
    """One `GET /list_jobs` item as the FL API would return it."""
    return IJobMetaData(job_id=_BACKEND_JOB_ID, status=status, status_details=details)


@pytest.fixture
def deps():
    """Every collaborator of the sweep, patched. Defaults describe a live, unretried, in-flight job."""
    settings = MagicMock()
    settings.FL_JOB_UNLISTED_GRACE_MINUTES = _GRACE_MINUTES
    with (
        patch(f"{_MODULE}._load_in_flight_jobs") as load,
        patch(f"{_MODULE}._reread_statuses") as reread,
        patch(f"{_MODULE}._is_models_newest_job") as newest,
        patch(f"{_MODULE}._release_job") as release,
        patch(f"{_MODULE}.get_backend_job_metadata") as status,
        patch(f"{_MODULE}.fetch_run_logs") as logs,
        patch(f"{_MODULE}.add_log") as add_log,
        patch(f"{_MODULE}.update_model_status") as update,
        patch(f"{_MODULE}.get_settings", return_value=settings),
    ):
        load.return_value = []
        reread.return_value = (ModelStatus.RUNNING, JobStatus.IN_PROGRESS)
        newest.return_value = True
        logs.return_value = None
        update.return_value = ModelStatus.ERROR
        yield {
            "load": load,
            "reread": reread,
            "newest": newest,
            "release": release,
            "status": status,
            "logs": logs,
            "add_log": add_log,
            "update_model_status": update,
        }


def _assert_left_alone(deps, db):
    deps["add_log"].assert_not_called()
    deps["update_model_status"].assert_not_called()
    deps["release"].assert_not_called()
    # A job the sweep leaves alone must not have tripped the per-job error path either — a
    # fall-through that raises is swallowed by that path and would otherwise look identical.
    db.rollback.assert_not_called()


# --- a failed run ---------------------------------------------------------------------------


def test_failed_run_errors_the_model_and_logs_the_cause(deps):
    db = MagicMock()
    job = _job()
    deps["load"].return_value = [job]
    deps["status"].return_value = _meta(FLJobStatus.FAILED)
    deps["logs"].return_value = RunLogTail(log="ImportError: cannot import name 'min_clients_from_run_config'")

    reported = reconcile_failed_fl_jobs(db)

    assert reported == 1
    deps["add_log"].assert_called_once()
    log_args, log_kwargs = deps["add_log"].call_args
    assert log_args[0] == job.model_id
    assert _BACKEND_JOB_ID in log_args[1]
    assert "min_clients_from_run_config" in log_args[1]
    assert log_kwargs["success"] is False
    # transaction=session defers add_log's commit to update_model_status's own, so a failed
    # status write can never leave an orphaned feed row for the next tick to duplicate.
    assert log_kwargs["transaction"] is db
    deps["update_model_status"].assert_called_once_with(job.model_id, ModelStatus.ERROR, db)


def test_a_declined_status_write_is_rolled_back_and_not_counted(deps):
    # update_model_status returns the model's *current* status without committing when a
    # late transition is ignored (the model went STOPPED between the re-read and its own
    # session.get). The feed row added under transaction=session is then pending in the
    # session: left there, a later job's commit in the same tick would flush "Training
    # failed" onto a model the user stopped.
    db = MagicMock()
    deps["load"].return_value = [_job()]
    deps["status"].return_value = _meta(FLJobStatus.FAILED)
    deps["update_model_status"].return_value = ModelStatus.STOPPED

    reported = reconcile_failed_fl_jobs(db)

    assert reported == 0
    db.rollback.assert_called_once()


@pytest.mark.parametrize(
    ("fl_backend", "expected_hint"),
    [
        (FLBackend.FLOWER, "flwr log <run-id> local --show"),
        # fl-api-base serves no /run_logs yet; NVFLARE does keep the server job log, so the
        # hint says where it is rather than claiming it does not exist.
        (FLBackend.NVFLARE, "deploy-fl-server-net-<n>-1"),
    ],
)
def test_failure_without_retrievable_logs_names_the_backend_manual_fallback(deps, fl_backend, expected_hint):
    db = MagicMock()
    deps["load"].return_value = [_job(fl_backend=fl_backend)]
    deps["status"].return_value = _meta(FLJobStatus.FAILED)
    deps["logs"].return_value = None

    reconcile_failed_fl_jobs(db)

    message = deps["add_log"].call_args[0][1]
    assert "could not be retrieved" in message
    assert expected_hint in message


def test_an_empty_log_is_reported_as_empty_not_as_unretrievable(deps):
    # The FL API answered with a log that has nothing in it. That is a different fact from
    # "the FL API could not be reached", and the row must not send the reader to re-run a
    # command that will print the same nothing.
    db = MagicMock()
    deps["load"].return_value = [_job()]
    deps["status"].return_value = _meta(FLJobStatus.FAILED)
    deps["logs"].return_value = RunLogTail(log="   \n")

    reconcile_failed_fl_jobs(db)

    message = deps["add_log"].call_args[0][1]
    assert "returned an empty log" in message
    assert "could not be retrieved" not in message


def test_a_truncated_tail_says_so_and_where_the_rest_is(deps):
    db = MagicMock()
    deps["load"].return_value = [_job()]
    deps["status"].return_value = _meta(FLJobStatus.FAILED)
    deps["logs"].return_value = RunLogTail(log="Traceback...\nImportError", truncated=True)

    reconcile_failed_fl_jobs(db)

    message = deps["add_log"].call_args[0][1]
    assert "ImportError" in message
    assert "truncated" in message
    assert "flwr log" in message


def test_hub_caps_the_stored_tail_from_the_end(deps):
    # Belt and braces against an FL API that does not truncate: the cap keeps the END of the
    # log (where the traceback is), not the head.
    db = MagicMock()
    deps["load"].return_value = [_job()]
    deps["status"].return_value = _meta(FLJobStatus.FAILED)
    deps["logs"].return_value = RunLogTail(log="HEAD\n" + "x" * 9000 + "\nImportError: TAIL")

    reconcile_failed_fl_jobs(db)

    message = deps["add_log"].call_args[0][1]
    assert "ImportError: TAIL" in message
    assert "HEAD" not in message
    assert len(message.split("End of the FL run log:\n", 1)[1]) <= 8000


def test_backend_status_details_lead_the_message(deps):
    """The backend's one-line cause goes above the log tail, not after it.

    A Flower run log opens with the per-run `uv sync` -- one `Installed: [...]` line is most
    of the stored row -- so a reader who only sees the top of the feed panel would otherwise
    get a dependency manifest instead of the reason.
    """
    db = MagicMock()
    deps["load"].return_value = [_job()]
    deps["status"].return_value = _meta(
        FLJobStatus.FAILED,
        details="ServerApp failed with exception: No module named 'flwr.common.message'",
    )
    deps["logs"].return_value = RunLogTail(log="Installed: [a==1, b==2]\nTraceback...\nModuleNotFoundError")

    reconcile_failed_fl_jobs(db)

    message = deps["add_log"].call_args[0][1]
    assert "Reported cause: ServerApp failed with exception: No module named" in message
    # Cause first, log tail after -- the ordering is the point.
    assert message.index("Reported cause:") < message.index("End of the FL run log:")


def test_status_details_are_reported_even_without_a_log(deps):
    """An NVFLARE-style failure (no /run_logs) still gets any cause the backend supplied."""
    db = MagicMock()
    deps["load"].return_value = [_job(fl_backend=FLBackend.NVFLARE)]
    deps["status"].return_value = _meta(FLJobStatus.FAILED, details="job failed to deploy")
    deps["logs"].return_value = None

    reconcile_failed_fl_jobs(db)

    message = deps["add_log"].call_args[0][1]
    assert "Reported cause: job failed to deploy" in message
    assert "deploy-fl-server-net-<n>-1" in message


def test_absent_status_details_change_nothing(deps):
    """A backend with no per-job explanation (NVFLARE today) reads exactly as before."""
    db = MagicMock()
    deps["load"].return_value = [_job()]
    deps["status"].return_value = _meta(FLJobStatus.FAILED, details=None)
    deps["logs"].return_value = RunLogTail(log="Traceback...\nImportError")

    reconcile_failed_fl_jobs(db)

    message = deps["add_log"].call_args[0][1]
    assert "Reported cause:" not in message
    assert "End of the FL run log:" in message


# --- a run stopped outside FLIP -----------------------------------------------------------


def test_run_stopped_outside_flip_is_resolved(deps):
    # The hub's own abort DELETEs the job before it asks the backend to stop the run, so a
    # job still IN_PROGRESS whose run the backend reports as STOPPED can only have been
    # stopped out of band (`flwr stop` / the FLARE admin console). Such a run never reports.
    db = MagicMock()
    job = _job()
    deps["load"].return_value = [job]
    deps["status"].return_value = _meta(FLJobStatus.STOPPED)

    reported = reconcile_failed_fl_jobs(db)

    assert reported == 1
    deps["logs"].assert_not_called()
    message = deps["add_log"].call_args[0][1]
    assert "stopped outside FLIP" in message
    assert _BACKEND_JOB_ID in message
    deps["update_model_status"].assert_called_once_with(job.model_id, ModelStatus.ERROR, db)


def test_run_stopped_by_the_hubs_own_abort_is_left_to_it(deps):
    # Same backend status, but the job re-reads as DELETED: the hub's abort path is mid-flight
    # (its dequeue commits before the stop is sent and the model goes STOPPED after), so it
    # owns the outcome and the sweep must not race it with an ERROR.
    db = MagicMock()
    deps["load"].return_value = [_job()]
    deps["status"].return_value = _meta(FLJobStatus.STOPPED)
    deps["reread"].return_value = (ModelStatus.RUNNING, JobStatus.DELETED)

    reported = reconcile_failed_fl_jobs(db)

    assert reported == 0
    _assert_left_alone(deps, db)


# --- statuses the sweep does not act on ---------------------------------------------------


@pytest.mark.parametrize("backend_status", [FLJobStatus.RUNNING, FLJobStatus.PENDING, FLJobStatus.FINISHED, None])
def test_live_runs_are_left_alone(deps, backend_status):
    # FINISHED in particular: a run whose ServerApp has finished is routinely still uploading
    # results, and the hub's own RESULTS_UPLOADED callback owns that transition. None: within
    # the grace period (the job's `started` defaults to just-now) an unlisted run is a listing
    # hiccup, not a death.
    db = MagicMock()
    deps["load"].return_value = [_job()]
    deps["status"].return_value = _meta(backend_status) if backend_status else None

    reported = reconcile_failed_fl_jobs(db)

    assert reported == 0
    deps["logs"].assert_not_called()
    _assert_left_alone(deps, db)


def test_unknown_status_is_left_alone_but_not_silently(deps, caplog):
    # An unmapped native status must never be acted on -- but a model that will sit in flight
    # because of it must show up in the hub's own log, not only in the fl-api container's.
    db = MagicMock()
    deps["load"].return_value = [_job()]
    deps["status"].return_value = _meta(FLJobStatus.UNKNOWN)

    with caplog.at_level("WARNING"):
        reported = reconcile_failed_fl_jobs(db)

    assert reported == 0
    _assert_left_alone(deps, db)
    assert any(_BACKEND_JOB_ID in record.message and "UNKNOWN" in record.message for record in caplog.records)


def test_finished_run_that_never_reported_is_flagged_after_the_grace(deps, caplog):
    # A run the backend has marked finished while the hub still waits on it: the results
    # callback never landed. Not acted on (the run may still be uploading), but past the grace
    # it is worth a line an operator can find.
    db = MagicMock()
    deps["load"].return_value = [_job(started=datetime.utcnow() - timedelta(minutes=_GRACE_MINUTES + 5))]
    deps["status"].return_value = _meta(FLJobStatus.FINISHED)

    with caplog.at_level("WARNING"):
        reported = reconcile_failed_fl_jobs(db)

    assert reported == 0
    _assert_left_alone(deps, db)
    assert any(_BACKEND_JOB_ID in record.message and "never reported" in record.message for record in caplog.records)


# --- a run the backend no longer lists ------------------------------------------------------


def test_unlisted_run_past_grace_is_resolved_without_a_log_fetch(deps):
    # A SuperLink restart loses its in-memory run state: the run is gone, its log with it,
    # and it can never report. Past the grace period that is a death, not a hiccup.
    db = MagicMock()
    job = _job(started=datetime.utcnow() - timedelta(minutes=_GRACE_MINUTES + 5))
    deps["load"].return_value = [job]
    deps["status"].return_value = None

    reported = reconcile_failed_fl_jobs(db)

    assert reported == 1
    deps["logs"].assert_not_called()
    message = deps["add_log"].call_args[0][1]
    assert "no longer listed" in message
    assert _BACKEND_JOB_ID in message
    deps["update_model_status"].assert_called_once_with(job.model_id, ModelStatus.ERROR, db)


def test_unlisted_run_inside_the_grace_is_a_hiccup_not_a_death(deps):
    # A realistic age, not just-now: a units slip (seconds for minutes) would pass a 0s test.
    db = MagicMock()
    deps["load"].return_value = [_job(started=datetime.utcnow() - timedelta(minutes=_GRACE_MINUTES - 1))]
    deps["status"].return_value = None

    reported = reconcile_failed_fl_jobs(db)

    assert reported == 0
    _assert_left_alone(deps, db)


def test_unlisted_run_with_no_started_timestamp_is_left_alone(deps):
    # `started` is set at job pickup so an in-flight job should always carry one; if it
    # somehow doesn't, there is nothing to measure the grace period from -- do nothing.
    db = MagicMock()
    deps["load"].return_value = [_job()._replace(started=None)]
    deps["status"].return_value = None

    reported = reconcile_failed_fl_jobs(db)

    assert reported == 0
    _assert_left_alone(deps, db)


def test_an_unreachable_backend_is_an_error_not_an_unlisted_run(deps):
    # "Unlisted" means a 200 without the run. A backend that cannot be reached tells the
    # sweep nothing, and must never be read as "the run is gone" (which past the grace
    # would error every model on that net).
    db = MagicMock()
    deps["load"].return_value = [_job(started=datetime.utcnow() - timedelta(hours=2))]
    deps["status"].side_effect = httpx.ConnectError("net-1 is down")

    reported = reconcile_failed_fl_jobs(db)

    assert reported == 0
    deps["add_log"].assert_not_called()
    deps["update_model_status"].assert_not_called()
    db.rollback.assert_called_once()


# --- the races --------------------------------------------------------------------------------


def test_no_in_flight_jobs_does_not_call_the_fl_api(deps):
    db = MagicMock()

    assert reconcile_failed_fl_jobs(db) == 0
    deps["status"].assert_not_called()


@pytest.mark.parametrize(
    "settled_status",
    [ModelStatus.RESULTS_UPLOADED, ModelStatus.STOPPED, ModelStatus.ERROR],
)
def test_model_that_settled_during_the_status_call_wins_the_race(deps, settled_status):
    db = MagicMock()
    deps["load"].return_value = [_job()]
    deps["status"].return_value = _meta(FLJobStatus.FAILED)
    deps["reread"].return_value = (settled_status, JobStatus.IN_PROGRESS)

    reported = reconcile_failed_fl_jobs(db)

    assert reported == 0
    _assert_left_alone(deps, db)


def test_unlisted_run_past_grace_still_defers_to_a_settled_model(deps):
    db = MagicMock()
    deps["load"].return_value = [_job(started=datetime.utcnow() - timedelta(minutes=_GRACE_MINUTES + 5))]
    deps["status"].return_value = None
    deps["reread"].return_value = (ModelStatus.STOPPED, JobStatus.IN_PROGRESS)

    reported = reconcile_failed_fl_jobs(db)

    assert reported == 0
    _assert_left_alone(deps, db)


# --- a retried model, and a net the last resolution leaked ---------------------------------


def test_dead_stale_job_of_a_retried_model_frees_its_net_without_touching_the_model(deps):
    # The model was re-initiated while this older job was still in flight, so it now has a
    # newer job. update_model_status(ERROR) would complete the model's NEWEST job -- the
    # healthy retry -- and free its net (the hazard fl_scheduler_service guards with the same
    # check). The stale job only needs its own net back.
    db = MagicMock()
    job = _job()
    deps["load"].return_value = [job]
    deps["status"].return_value = _meta(FLJobStatus.FAILED)
    deps["newest"].return_value = False

    reported = reconcile_failed_fl_jobs(db)

    assert reported == 0
    deps["release"].assert_called_once_with(job, db)
    deps["add_log"].assert_not_called()
    deps["update_model_status"].assert_not_called()


def test_leaked_net_of_an_already_errored_model_is_released_without_a_poll(deps):
    # update_model_status commits the status, then update_fl_scheduler commits the job/net
    # release separately. A DB blip between the two leaves model ERROR / job IN_PROGRESS /
    # net BUSY -- a state nothing else revisits. The sweep's own selection must include it.
    db = MagicMock()
    job = _job(model_status=ModelStatus.ERROR)
    deps["load"].return_value = [job]

    reported = reconcile_failed_fl_jobs(db)

    assert reported == 0
    deps["status"].assert_not_called()
    deps["release"].assert_called_once_with(job, db)
    deps["add_log"].assert_not_called()
    deps["update_model_status"].assert_not_called()


# --- robustness ------------------------------------------------------------------------------


def test_one_unreachable_net_does_not_stop_the_other_jobs(deps):
    db = MagicMock()
    unreachable, healthy = _job(), _job(backend_job_id="222")
    deps["load"].return_value = [unreachable, healthy]
    deps["status"].side_effect = [ConnectionError("net-1 is down"), _meta(FLJobStatus.FAILED)]

    reported = reconcile_failed_fl_jobs(db)

    assert reported == 1
    db.rollback.assert_called_once()
    deps["update_model_status"].assert_called_once_with(healthy.model_id, ModelStatus.ERROR, db)


def test_a_failed_fl_api_response_is_logged_with_its_body(deps, caplog):
    # A 500 from /list_jobs carries the adapter's `detail` (e.g. the flwr stderr); the sweep's
    # catch-all must keep it, or the reason lives only in the fl-api container.
    db = MagicMock()
    deps["load"].return_value = [_job()]
    request = httpx.Request("GET", f"{_ENDPOINT}/list_jobs")
    response = httpx.Response(500, request=request, text='{"detail": "Flower list failed: superlink refused"}')
    deps["status"].side_effect = httpx.HTTPStatusError("boom", request=request, response=response)

    with caplog.at_level("ERROR"):
        reconcile_failed_fl_jobs(db)

    assert any("superlink refused" in record.message for record in caplog.records)


def test_scheduled_task_never_raises():
    with patch(f"{_MODULE}.get_engine", side_effect=RuntimeError("no db")):
        reconcile_failed_fl_jobs_scheduled_task()
