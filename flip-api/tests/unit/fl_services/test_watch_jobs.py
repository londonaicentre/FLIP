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

from dataclasses import replace
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from uuid import uuid4

import httpx
import pytest

from flip_api.domain.interfaces.fl import IJobMetaData
from flip_api.domain.schemas.status import FLJobStatus
from flip_api.fl_services import watch_jobs
from flip_api.fl_services.watch_jobs import JobWatchdog, WatchedJob, why_job_is_dead

NOW = datetime(2026, 10, 14, 12, 0, 0)
PREPARE_TIMEOUT = timedelta(minutes=30)


def _job(fl_backend_job_id: str | None = "run-1", started: datetime | None = NOW - timedelta(hours=1)) -> WatchedJob:
    return WatchedJob(
        job_id=uuid4(),
        model_id=uuid4(),
        fl_backend_job_id=fl_backend_job_id,
        started=started,
        net_name="net-1",
        net_endpoint="http://fl-api-net-1:8000",
    )


def _listed(status: FLJobStatus, job_id: str = "run-1") -> list[IJobMetaData]:
    return [IJobMetaData(job_id=job_id, status=status)]


@pytest.mark.parametrize("status", [FLJobStatus.RUNNING, FLJobStatus.PENDING])
def test_a_running_or_waiting_job_is_alive(status):
    # PENDING covers NVFLARE waiting for resources (#1423), which can last ten scheduler tries.
    assert why_job_is_dead(_job(), _listed(status), NOW, PREPARE_TIMEOUT) is None


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (FLJobStatus.FAILED, "The FL server on net-1 reports this job failed before it reported a result."),
        (FLJobStatus.STOPPED, "The FL server on net-1 stopped this job before it reported a result."),
        (FLJobStatus.FINISHED, "The FL server on net-1 finished this job, but its result never reached FLIP."),
    ],
)
def test_a_job_the_backend_has_ended_is_dead(status, expected):
    assert why_job_is_dead(_job(), _listed(status), NOW, PREPARE_TIMEOUT) == expected


def test_a_job_the_backend_no_longer_lists_is_dead():
    reason = why_job_is_dead(_job(), _listed(FLJobStatus.RUNNING, job_id="another-run"), NOW, PREPARE_TIMEOUT)

    assert reason == "The FL server on net-1 no longer has this job, for example because it restarted."


def test_an_unreachable_fl_api_says_nothing():
    assert why_job_is_dead(_job(), None, NOW, PREPARE_TIMEOUT) is None


def test_any_live_record_keeps_the_job_alive():
    backend_jobs = _listed(FLJobStatus.FAILED) + _listed(FLJobStatus.RUNNING)

    assert why_job_is_dead(_job(), backend_jobs, NOW, PREPARE_TIMEOUT) is None


def test_a_job_still_being_prepared_is_alive():
    job = _job(fl_backend_job_id=None, started=NOW - timedelta(minutes=29))

    assert why_job_is_dead(job, None, NOW, PREPARE_TIMEOUT) is None


def test_a_job_never_submitted_after_the_timeout_is_dead():
    job = _job(fl_backend_job_id=None, started=NOW - timedelta(minutes=31))

    reason = why_job_is_dead(job, None, NOW, PREPARE_TIMEOUT)

    assert reason == "Training never reached the FL server on net-1: it was still being prepared after 30 minutes."


@pytest.fixture
def watched():
    job = _job()
    with (
        patch("flip_api.fl_services.watch_jobs.find_watched_jobs", return_value=[job]) as find,
        patch("flip_api.fl_services.watch_jobs.list_backend_jobs") as list_jobs,
        patch("flip_api.fl_services.watch_jobs._fail_job", return_value=True) as fail,
    ):
        yield {"job": job, "find": find, "list_jobs": list_jobs, "fail": fail}


def test_a_dead_job_is_failed_on_the_third_tick(watched):
    watched["list_jobs"].return_value = _listed(FLJobStatus.FAILED)
    watchdog = JobWatchdog(strikes=3, prepare_timeout=PREPARE_TIMEOUT)

    assert watchdog.tick(MagicMock(), NOW) == []
    assert watchdog.tick(MagicMock(), NOW) == []
    assert watchdog.tick(MagicMock(), NOW) == [watched["job"].model_id]
    watched["fail"].assert_called_once()
    assert watched["fail"].call_args.args[1] == (
        "The FL server on net-1 reports this job failed before it reported a result."
    )


def test_a_live_tick_resets_the_count(watched):
    watchdog = JobWatchdog(strikes=3, prepare_timeout=PREPARE_TIMEOUT)
    for status in (FLJobStatus.FAILED, FLJobStatus.FAILED, FLJobStatus.RUNNING, FLJobStatus.FAILED, FLJobStatus.FAILED):
        watched["list_jobs"].return_value = _listed(status)
        watchdog.tick(MagicMock(), NOW)

    watched["fail"].assert_not_called()


def test_an_unreachable_fl_api_never_fails_a_job(watched):
    # Review Focus 1: an FL API down for many ticks (a redeploy, an outage) fails nothing.
    watched["list_jobs"].return_value = None
    watchdog = JobWatchdog(strikes=3, prepare_timeout=PREPARE_TIMEOUT)

    for _ in range(10):
        assert watchdog.tick(MagicMock(), NOW) == []
    watched["fail"].assert_not_called()


def test_each_net_is_listed_once_per_tick(watched):
    second = _job(fl_backend_job_id="run-2")
    watched["find"].return_value = [watched["job"], second]
    watched["list_jobs"].return_value = _listed(FLJobStatus.RUNNING) + _listed(FLJobStatus.RUNNING, job_id="run-2")

    JobWatchdog(strikes=3, prepare_timeout=PREPARE_TIMEOUT).tick(MagicMock(), NOW)

    watched["list_jobs"].assert_called_once_with("http://fl-api-net-1:8000")


def test_a_net_with_only_unsubmitted_jobs_is_not_listed(watched):
    watched["find"].return_value = [_job(fl_backend_job_id=None)]

    JobWatchdog(strikes=3, prepare_timeout=PREPARE_TIMEOUT).tick(MagicMock(), NOW)

    watched["list_jobs"].assert_not_called()


def test_a_job_that_ends_on_its_own_is_forgotten(watched):
    watched["list_jobs"].return_value = _listed(FLJobStatus.FAILED)
    watchdog = JobWatchdog(strikes=3, prepare_timeout=PREPARE_TIMEOUT)
    watchdog.tick(MagicMock(), NOW)
    watchdog.tick(MagicMock(), NOW)

    # Its own terminal callback arrived: the hub no longer watches it.
    watched["find"].return_value = []
    watchdog.tick(MagicMock(), NOW)
    watched["find"].return_value = [watched["job"]]
    watchdog.tick(MagicMock(), NOW)

    watched["fail"].assert_not_called()


def test_the_scheduled_task_ticks_with_the_configured_watchdog():
    watch_jobs._watchdog.cache_clear()
    with (
        patch.object(watch_jobs, "get_settings") as settings,
        patch.object(watch_jobs, "get_engine"),
        patch.object(watch_jobs, "Session"),
        patch.object(watch_jobs.JobWatchdog, "tick") as tick,
    ):
        settings.return_value.FL_JOB_WATCHDOG_STRIKES = 5
        settings.return_value.FL_JOB_PREPARE_TIMEOUT_MINUTES = 45
        watch_jobs.watch_fl_jobs_scheduled_task()

    tick.assert_called_once()
    assert watch_jobs._watchdog().strikes == 5
    assert watch_jobs._watchdog().prepare_timeout == timedelta(minutes=45)
    watch_jobs._watchdog.cache_clear()


def test_the_scheduled_task_never_raises():
    with (
        patch.object(watch_jobs, "get_engine"),
        patch.object(watch_jobs, "Session", side_effect=RuntimeError("database down")),
    ):
        watch_jobs.watch_fl_jobs_scheduled_task()


def _on_net_2(job: WatchedJob) -> WatchedJob:
    return replace(job, net_name="net-2", net_endpoint="http://fl-api-net-2:8000")


def test_one_net_that_cannot_be_listed_does_not_stop_the_others(watched):
    # A non-HTTP error for one net (here a malformed endpoint) must not disable the watchdog for every net.
    other = _on_net_2(_job(fl_backend_job_id="run-2"))
    watched["find"].return_value = [watched["job"], other]

    def list_jobs(endpoint: str) -> list[IJobMetaData]:
        if endpoint == "http://fl-api-net-1:8000":
            raise httpx.InvalidURL("bad endpoint")
        return _listed(FLJobStatus.FAILED, job_id="run-2")

    watched["list_jobs"].side_effect = list_jobs

    failed = JobWatchdog(strikes=1, prepare_timeout=PREPARE_TIMEOUT).tick(MagicMock(), NOW)

    assert failed == [other.model_id]


def test_one_job_that_cannot_be_failed_does_not_stop_the_others(watched):
    other = _job(fl_backend_job_id="run-2")
    watched["find"].return_value = [watched["job"], other]
    watched["list_jobs"].return_value = _listed(FLJobStatus.FAILED) + _listed(FLJobStatus.FAILED, job_id="run-2")
    watched["fail"].side_effect = [RuntimeError("database hiccup"), True]
    session = MagicMock()

    failed = JobWatchdog(strikes=1, prepare_timeout=PREPARE_TIMEOUT).tick(session, NOW)

    assert failed == [other.model_id]
    session.rollback.assert_called_once()


def test_a_job_that_could_not_be_failed_is_retried_on_the_next_tick(watched):
    watched["list_jobs"].return_value = _listed(FLJobStatus.FAILED)
    watched["fail"].side_effect = [RuntimeError("database hiccup"), True]
    watchdog = JobWatchdog(strikes=3, prepare_timeout=PREPARE_TIMEOUT)

    results = [watchdog.tick(MagicMock(), NOW) for _ in range(4)]

    assert results == [[], [], [], [watched["job"].model_id]]
