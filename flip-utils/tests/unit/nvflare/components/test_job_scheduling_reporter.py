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

"""NVFLARE's scheduler verdict, relayed to the hub (FLIP#1390).

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
from flip.nvflare.components.job_scheduling_reporter import JobSchedulingReporter, scheduling_shortfall
from flip.schemas import FLLogEvent

MODEL_ID = "123e4567-e89b-12d3-a456-426614174000"
GPU_REQ = {"num_of_gpus": 1, "mem_per_gpu_in_GiB": 16}


def _meta(sites=("GSTT", "AZ1"), schedule_count=0, model_id=MODEL_ID):
    meta = {
        "min_clients": len(sites),
        "mandatory_clients": list(sites),
        "schedule_count": schedule_count,
    }
    if model_id is not None:
        meta["custom_props"] = {"model_id": model_id}
    return meta


def _fl_ctx(meta, reqs, results, max_schedule_count=10, scheduler_readable=True):
    """A real FLContext, as the scheduler hands it over: NVFLARE's own logging rejects a mock one."""
    fl_ctx = FLContext()
    fl_ctx.set_prop(FLContextKey.JOB_META, meta, private=True, sticky=False)
    fl_ctx.set_prop(FLContextKey.CLIENT_RESOURCE_SPECS, reqs, private=True, sticky=False)
    fl_ctx.set_prop(FLContextKey.RESOURCE_CHECK_RESULT, results, private=True, sticky=False)
    engine = MagicMock()
    scheduler = MagicMock(max_schedule_count=max_schedule_count) if scheduler_readable else None
    engine.get_component.return_value = scheduler
    fl_ctx.set_prop(ReservedKey.ENGINE, engine, private=True, sticky=False)
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
    """NVFLARE schedules once min_clients and every mandatory site are satisfied; mirror that exactly."""
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
    """After max_schedule_count tries NVFLARE marks the job FINISHED:CAN_NOT_SCHEDULE without firing any event,
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


def test_a_job_that_will_be_scheduled_reports_nothing():
    flip = MagicMock()
    reporter = JobSchedulingReporter(flip=flip)
    fl_ctx = _fl_ctx(_meta(), {"GSTT": GPU_REQ, "AZ1": GPU_REQ}, {"GSTT": (True, "a"), "AZ1": (True, "b")})

    reporter.handle_event(EventType.AFTER_CHECK_CLIENT_RESOURCES, fl_ctx)

    flip.send_event.assert_not_called()
    flip.update_status.assert_not_called()


@pytest.mark.parametrize("model_id", [None, "not-a-uuid"])
def test_a_job_flip_did_not_submit_is_left_alone(model_id):
    """An admin can submit a plain NVFLARE job to the same server; it has no FLIP model to report to."""
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


def test_a_failure_while_reporting_never_reaches_the_scheduler():
    """This runs inside NVFLARE's scheduling pass: raising would abort scheduling for every queued job."""
    flip = MagicMock()
    flip.send_event.side_effect = RuntimeError("hub down")
    reporter = JobSchedulingReporter(flip=flip)
    fl_ctx = _fl_ctx(_meta(), {"AZ1": GPU_REQ}, {"AZ1": (False, "")})

    reporter.handle_event(EventType.AFTER_CHECK_CLIENT_RESOURCES, fl_ctx)  # does not raise
