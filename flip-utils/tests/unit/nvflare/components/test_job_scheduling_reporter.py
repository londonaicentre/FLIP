# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
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

"""FLARE's scheduler verdict, relayed to the hub (FLIP#1390).

The scheduler fires AFTER_CHECK_CLIENT_RESOURCES in the server's parent process once every site has
answered whether it can run the job. These tests stand in for that context: the job's meta, what each
site was asked for, and what each site answered.
"""

from unittest.mock import MagicMock

import pytest
from nvflare.apis.event_type import EventType
from nvflare.apis.fl_constant import FLContextKey, ReservedKey
from nvflare.apis.fl_context import FLContext

from flip.constants import ModelStatus
from flip.nvflare.components import job_scheduling_reporter
from flip.nvflare.components.job_scheduling_reporter import (
    FINAL_STATUS_RETRY_DELAYS_SECONDS,
    JobSchedulingReporter,
    scheduling_shortfall,
)
from flip.schemas import FLLogEvent

MODEL_ID = "123e4567-e89b-12d3-a456-426614174000"
GPU_REQ = {"num_of_gpus": 1, "mem_per_gpu_in_GiB": 16}


def _meta(sites=("GSTT", "AZ1"), schedule_count=0, model_id=MODEL_ID):
    """A job's meta as the fl-api writes it: the job is named after the hub's model id."""
    meta = {
        "min_clients": len(sites),
        "mandatory_clients": list(sites),
        "schedule_count": schedule_count,
    }
    if model_id is not None:
        meta["name"] = model_id
    return meta


def _fl_ctx(meta, reqs, results, max_schedule_count=10, scheduler_readable=True, engine=True):
    """A real FLContext, as the scheduler hands it over: FLARE's own logging rejects a mock one."""
    fl_ctx = FLContext()
    fl_ctx.set_prop(FLContextKey.JOB_META, meta, private=True, sticky=False)
    fl_ctx.set_prop(FLContextKey.CLIENT_RESOURCE_SPECS, reqs, private=True, sticky=False)
    fl_ctx.set_prop(FLContextKey.RESOURCE_CHECK_RESULT, results, private=True, sticky=False)
    if engine:
        server_engine = MagicMock()
        scheduler = MagicMock(max_schedule_count=max_schedule_count) if scheduler_readable else None
        server_engine.get_component.return_value = scheduler
        fl_ctx.set_prop(ReservedKey.ENGINE, server_engine, private=True, sticky=False)
    return fl_ctx


# ── the verdict ─────────────────────────────────────────────────────────────────────


def test_a_job_every_site_can_run_has_no_shortfall():
    reqs = {"GSTT": GPU_REQ, "AZ1": GPU_REQ}
    results = {"GSTT": (True, "t1"), "AZ1": (True, "t2")}

    assert scheduling_shortfall(_meta(), reqs, results) is None


def test_the_site_without_the_gpu_is_named_with_what_it_was_asked_for():
    reqs = {"GSTT": GPU_REQ, "AZ1": GPU_REQ}
    results = {"GSTT": (True, "t1"), "AZ1": (False, "")}

    assert scheduling_shortfall(_meta(), reqs, results) == {"AZ1": GPU_REQ}


def test_a_site_that_never_answered_counts_as_short():
    reqs = {"GSTT": GPU_REQ, "AZ1": GPU_REQ}

    assert scheduling_shortfall(_meta(), reqs, {"GSTT": (True, "t1")}) == {"AZ1": GPU_REQ}


def test_no_answers_at_all_leave_every_site_short():
    reqs = {"GSTT": GPU_REQ, "AZ1": GPU_REQ}

    assert scheduling_shortfall(_meta(), reqs, {}) == {"GSTT": GPU_REQ, "AZ1": GPU_REQ}


def test_an_optional_site_saying_no_does_not_stop_a_job_that_has_enough_sites():
    """FLARE schedules once min_clients and every mandatory site are satisfied; mirror that exactly."""
    meta = _meta(sites=("GSTT", "KCH", "AZ1"))
    meta["min_clients"] = 2
    meta["mandatory_clients"] = ["GSTT"]
    reqs = {"GSTT": GPU_REQ, "KCH": GPU_REQ, "AZ1": GPU_REQ}
    results = {"GSTT": (True, "t1"), "KCH": (True, "t2"), "AZ1": (False, "")}

    assert scheduling_shortfall(meta, reqs, results) is None


def test_a_mandatory_site_saying_no_stops_the_job_even_with_enough_sites():
    meta = _meta(sites=("GSTT", "KCH", "AZ1"))
    meta["min_clients"] = 2
    meta["mandatory_clients"] = ["AZ1"]
    reqs = {"GSTT": GPU_REQ, "KCH": GPU_REQ, "AZ1": GPU_REQ}
    results = {"GSTT": (True, "t1"), "KCH": (True, "t2"), "AZ1": (False, "")}

    assert scheduling_shortfall(meta, reqs, results) == {"AZ1": GPU_REQ}


def test_a_meta_without_min_clients_needs_one_site_as_nvflare_assumes():
    """FLARE builds the job with ``meta.get("min_clients", 1)`` (nvflare/apis/job_def.py)."""
    meta = {"name": MODEL_ID}
    reqs = {"GSTT": GPU_REQ, "AZ1": GPU_REQ}

    assert scheduling_shortfall(meta, reqs, {"GSTT": (False, ""), "AZ1": (False, "")}) == reqs
    assert scheduling_shortfall(meta, reqs, {"GSTT": (True, "t1"), "AZ1": (False, "")}) is None


# ── relaying it ─────────────────────────────────────────────────────────────────────


def test_a_refused_attempt_tells_the_hub_who_fell_short_and_which_try_it_was():
    flip = MagicMock()
    reporter = JobSchedulingReporter(flip=flip)
    fl_ctx = _fl_ctx(
        _meta(schedule_count=1), {"GSTT": GPU_REQ, "AZ1": GPU_REQ}, {"GSTT": (True, "t"), "AZ1": (False, "")}
    )

    reporter.handle_event(EventType.AFTER_CHECK_CLIENT_RESOURCES, fl_ctx)

    flip.send_event.assert_called_once_with(
        model_id=MODEL_ID,
        event_type=FLLogEvent.JOB_WAITING_FOR_RESOURCES,
        global_round=None,
        client_name="AZ1",
        details={"attempt": 2, "max_attempts": 10, "final": False, "requested": GPU_REQ},
        success=True,
    )
    flip.update_status.assert_not_called()


def test_each_site_that_fell_short_gets_its_own_row_so_the_hub_names_the_trust():
    """The hub resolves an event's FL client name to the trust and shows that trust beside the row."""
    flip = MagicMock()
    reporter = JobSchedulingReporter(flip=flip)
    reqs = {"GSTT": GPU_REQ, "AZ1": GPU_REQ, "KCH": {"num_of_gpus": 2, "mem_per_gpu_in_GiB": 16}}
    meta = _meta(sites=("GSTT", "AZ1", "KCH"))
    fl_ctx = _fl_ctx(meta, reqs, {"GSTT": (True, "t"), "AZ1": (False, ""), "KCH": (False, "")})

    reporter.handle_event(EventType.AFTER_CHECK_CLIENT_RESOURCES, fl_ctx)

    sent = {c.kwargs["client_name"]: c.kwargs["details"]["requested"] for c in flip.send_event.call_args_list}
    assert sent == {"AZ1": GPU_REQ, "KCH": reqs["KCH"]}


def test_the_last_attempt_the_scheduler_will_make_fails_the_model():
    """After max_schedule_count tries FLARE marks the job FINISHED:CAN_NOT_SCHEDULE without firing any event,
    and the job never starts, so nothing inside it can tell the hub. The last refused try has to."""
    flip = MagicMock()
    reporter = JobSchedulingReporter(flip=flip)
    fl_ctx = _fl_ctx(
        _meta(schedule_count=4), {"GSTT": GPU_REQ, "AZ1": GPU_REQ}, {"AZ1": (False, "")}, max_schedule_count=5
    )

    reporter.handle_event(EventType.AFTER_CHECK_CLIENT_RESOURCES, fl_ctx)

    details = flip.send_event.call_args.kwargs["details"]
    assert details["final"] is True
    assert flip.send_event.call_args.kwargs["client_name"] == "AZ1"
    assert details["attempt"] == details["max_attempts"] == 5
    assert flip.send_event.call_args.kwargs["success"] is False
    flip.update_status.assert_called_once_with(MODEL_ID, ModelStatus.ERROR)


def _last_attempt_ctx():
    return _fl_ctx(_meta(schedule_count=4), {"AZ1": GPU_REQ}, {"AZ1": (False, "")}, max_schedule_count=5)


@pytest.fixture
def waits(monkeypatch):
    """The seconds the reporter sleeps for, recorded instead of slept."""
    recorded = []
    monkeypatch.setattr(job_scheduling_reporter.time, "sleep", recorded.append)
    return recorded


def test_the_models_error_is_retried_until_the_hub_accepts_it(waits):
    """That ERROR is all that frees the model's net, and the reporter gets no later chance to send it."""
    flip = MagicMock()
    flip.update_status.side_effect = [False, False, True]
    reporter = JobSchedulingReporter(flip=flip)

    reporter.handle_event(EventType.AFTER_CHECK_CLIENT_RESOURCES, _last_attempt_ctx())

    assert flip.update_status.call_count == 3
    assert waits == list(FINAL_STATUS_RETRY_DELAYS_SECONDS[:2])


def test_a_hub_that_never_accepts_it_is_given_up_on_with_the_way_to_free_the_net(waits):
    flip = MagicMock()
    flip.update_status.return_value = False
    reporter = JobSchedulingReporter(flip=flip)
    reporter.log_error = MagicMock()

    reporter.handle_event(EventType.AFTER_CHECK_CLIENT_RESOURCES, _last_attempt_ctx())

    assert flip.update_status.call_count == len(FINAL_STATUS_RETRY_DELAYS_SECONDS) + 1
    assert waits == list(FINAL_STATUS_RETRY_DELAYS_SECONDS)
    message = reporter.log_error.call_args.args[1]
    assert MODEL_ID in message
    assert "Abort job" in message


def test_a_last_try_that_names_no_site_still_says_why_the_model_failed():
    """With nothing checked there is no site to name; the model must not fail with an empty activity feed.

    Left without ``requested``, the hub renders the row as "Training could not start · FLARE gave up after 5
    tries"; an empty ``requested`` would read "this trust did not confirm its resources", naming no trust.
    """
    flip = MagicMock()
    reporter = JobSchedulingReporter(flip=flip)
    fl_ctx = _fl_ctx(_meta(schedule_count=4), {}, {}, max_schedule_count=5)

    reporter.handle_event(EventType.AFTER_CHECK_CLIENT_RESOURCES, fl_ctx)

    flip.send_event.assert_called_once_with(
        model_id=MODEL_ID,
        event_type=FLLogEvent.JOB_WAITING_FOR_RESOURCES,
        global_round=None,
        client_name=None,
        details={"attempt": 5, "max_attempts": 5, "final": True},
        success=False,
    )
    flip.update_status.assert_called_once_with(MODEL_ID, ModelStatus.ERROR)


def test_an_earlier_try_that_names_no_site_is_reported_against_the_model():
    flip = MagicMock()
    reporter = JobSchedulingReporter(flip=flip)

    reporter.handle_event(EventType.AFTER_CHECK_CLIENT_RESOURCES, _fl_ctx(_meta(), {}, {}))

    assert flip.send_event.call_args.kwargs["client_name"] is None
    assert flip.send_event.call_args.kwargs["details"] == {"attempt": 1, "max_attempts": 10, "final": False}
    flip.update_status.assert_not_called()


def test_the_model_is_found_by_the_job_name_the_fl_api_gives_every_job():
    """The job's config.json is not deployed until the job starts, and custom_props no longer carries the id
    (FLIP#70), so the meta's ``name`` is the one place the scheduler's context holds it."""
    flip = MagicMock()
    reporter = JobSchedulingReporter(flip=flip)
    meta = {**_meta(), "custom_props": {"team": "radiology"}}

    reporter.handle_event(EventType.AFTER_CHECK_CLIENT_RESOURCES, _fl_ctx(meta, {"AZ1": GPU_REQ}, {"AZ1": (False, "")}))

    assert flip.send_event.call_args.kwargs["model_id"] == MODEL_ID


def test_a_job_that_will_be_scheduled_reports_nothing():
    flip = MagicMock()
    reporter = JobSchedulingReporter(flip=flip)
    fl_ctx = _fl_ctx(_meta(), {"GSTT": GPU_REQ, "AZ1": GPU_REQ}, {"GSTT": (True, "a"), "AZ1": (True, "b")})

    reporter.handle_event(EventType.AFTER_CHECK_CLIENT_RESOURCES, fl_ctx)

    flip.send_event.assert_not_called()
    flip.update_status.assert_not_called()


@pytest.mark.parametrize("model_id", [None, "hello-pt", "not-a-uuid"])
def test_a_job_flip_did_not_submit_is_left_alone(model_id):
    """An admin can submit a plain FLARE job to the same server; it has no FLIP model to report to."""
    flip = MagicMock()
    reporter = JobSchedulingReporter(flip=flip)
    fl_ctx = _fl_ctx(_meta(model_id=model_id), {"AZ1": GPU_REQ}, {"AZ1": (False, "")})

    reporter.handle_event(EventType.AFTER_CHECK_CLIENT_RESOURCES, fl_ctx)

    flip.send_event.assert_not_called()


def test_other_events_are_ignored():
    flip = MagicMock()
    reporter = JobSchedulingReporter(flip=flip)
    fl_ctx = _fl_ctx(_meta(), {"AZ1": GPU_REQ}, {"AZ1": (False, "")})

    reporter.handle_event(EventType.BEFORE_CHECK_CLIENT_RESOURCES, fl_ctx)

    flip.send_event.assert_not_called()


def test_without_a_readable_scheduler_nvflares_default_retry_count_applies():
    flip = MagicMock()
    reporter = JobSchedulingReporter(flip=flip)
    fl_ctx = _fl_ctx(_meta(schedule_count=0), {"AZ1": GPU_REQ}, {"AZ1": (False, "")}, scheduler_readable=False)

    reporter.handle_event(EventType.AFTER_CHECK_CLIENT_RESOURCES, fl_ctx)

    assert flip.send_event.call_args.kwargs["details"]["max_attempts"] == 10


@pytest.mark.parametrize("lookup", ["no engine", "raises"])
def test_a_scheduler_lookup_that_fails_still_reports_and_fails_the_model_on_nvflares_last_try(lookup):
    """A failed lookup must not skip the pass: on the last try the model's ERROR is all that frees its net."""
    flip = MagicMock()
    reporter = JobSchedulingReporter(flip=flip)
    fl_ctx = _fl_ctx(_meta(schedule_count=9), {"AZ1": GPU_REQ}, {"AZ1": (False, "")}, engine=lookup != "no engine")
    if lookup == "raises":
        fl_ctx.get_engine().get_component.side_effect = RuntimeError("engine shutting down")

    reporter.handle_event(EventType.AFTER_CHECK_CLIENT_RESOURCES, fl_ctx)

    assert flip.send_event.call_args.kwargs["details"]["max_attempts"] == 10
    flip.update_status.assert_called_once_with(MODEL_ID, ModelStatus.ERROR)


def test_a_failure_while_reporting_never_reaches_the_scheduler():
    """This runs inside FLARE's scheduling pass: raising would abort scheduling for every queued job."""
    flip = MagicMock()
    flip.send_event.side_effect = RuntimeError("hub down")
    reporter = JobSchedulingReporter(flip=flip)
    fl_ctx = _fl_ctx(_meta(), {"AZ1": GPU_REQ}, {"AZ1": (False, "")})

    reporter.handle_event(EventType.AFTER_CHECK_CLIENT_RESOURCES, fl_ctx)  # does not raise
