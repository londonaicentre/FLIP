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

"""Relay NVFLARE's scheduling verdict to the Central Hub (FLIP#1390).

A job's ``resource_spec`` states what it needs at each site, and each site's resource manager reports
what it has. NVFLARE's ``DefaultJobScheduler`` asks every site before it starts a job; when a site
says no, the job waits and is retried with a growing interval, and after ``max_schedule_count`` tries
it is marked ``FINISHED:CAN_NOT_SCHEDULE``. NVFLARE logs why, but only on the FL server, and the job
never starts, so no component inside it can tell the hub anything.

This component runs in the server's parent process instead, as a site-level component registered in
the server kit's ``local/flip__p_resources.json`` (``python -m flip.nvflare.server_site_config`` writes
it at container start, and NVFLARE adds its components to the provisioned ``resources.json`` ones). It
listens for ``AFTER_CHECK_CLIENT_RESOURCES``, the event the scheduler fires once every site has
answered, and when the job will not start it sends the hub a ``JOB_WAITING_FOR_RESOURCES`` event for
each site that fell short, saying what it was asked for. On the scheduler's last attempt it also fails
the model, since nothing else will, and retries that until the hub accepts it.

It reports and never decides: the scheduler's own rule (``min_clients`` and every mandatory site) is
mirrored only to know whether there is anything to report.
"""

import time
from typing import Any

from nvflare.apis.event_type import EventType
from nvflare.apis.fl_component import FLComponent
from nvflare.apis.fl_constant import FLContextKey
from nvflare.apis.fl_context import FLContext
from nvflare.apis.job_def import JobMetaKey

from flip import FLIP, FLIPBase
from flip.constants import ModelStatus
from flip.schemas import FLLogEvent
from flip.utils.utils import Utils

# DefaultJobScheduler's own default, used when the scheduler component cannot be read.
NVFLARE_DEFAULT_MAX_SCHEDULE_COUNT = 10

# On the scheduler's last try the model's ERROR is all that frees its net, and NVFLARE does not call the
# reporter for that job again, so the update is retried until the hub accepts it. Seconds to wait before each
# retry, about a minute in all: enough for a brief hub outage. The waits run inside the event handler, so they
# block the scheduler's thread: no other queued job on this server is tried until they end. That costs little
# while the hub gives a net one job at a time and this job is it, but weigh it before growing the budget.
FINAL_STATUS_RETRY_DELAYS_SECONDS = (5, 15, 40)


def scheduling_shortfall(
    job_meta: dict[str, Any],
    resource_reqs: dict[str, dict],
    check_results: dict[str, tuple[bool, str]],
) -> dict[str, dict] | None:
    """The sites that keep a job from starting, with what each was asked for.

    Mirrors ``DefaultJobScheduler._try_job``: the job starts when at least ``min_clients`` sites have
    enough resources and every ``mandatory_clients`` site is among them. A site that did not answer
    counts as not having enough, as it does for the scheduler.

    Args:
        job_meta (dict[str, Any]): the job's meta (``FLContextKey.JOB_META``).
        resource_reqs (dict[str, dict]): what each site was asked for (``FLContextKey.CLIENT_RESOURCE_SPECS``).
        check_results (dict[str, tuple[bool, str]]): each site's answer (``FLContextKey.RESOURCE_CHECK_RESULT``),
            ``(is_resource_enough, token)``.

    Returns:
        dict[str, dict] | None: ``{site: requested resources}`` for every site that said no, or None when
        the job will start.
    """
    ok_sites = {site for site, (enough, _token) in (check_results or {}).items() if enough}
    required = job_meta.get(JobMetaKey.MANDATORY_CLIENTS) or []
    # NVFLARE's own default (nvflare/apis/job_def.py job_from_meta).
    min_sites = job_meta.get(JobMetaKey.MIN_CLIENTS, 1)
    if check_results and len(ok_sites) >= min_sites and all(site in ok_sites for site in required):
        return None
    return {site: dict(req or {}) for site, req in resource_reqs.items() if site not in ok_sites}


class JobSchedulingReporter(FLComponent):
    """Tells the hub why NVFLARE's scheduler has not started a FLIP job.

    Args:
        job_scheduler_id (str): component id of the server's job scheduler, read for its
            ``max_schedule_count``. The provisioned server kit names it ``job_scheduler``.
        flip (FLIPBase | None): FLIP client used to reach the hub. Defaults to ``FLIP()``.
    """

    def __init__(self, job_scheduler_id: str = "job_scheduler", flip: FLIPBase | None = None):
        super().__init__()
        self.job_scheduler_id = job_scheduler_id
        self.flip = flip if flip is not None else FLIP()

    def handle_event(self, event_type: str, fl_ctx: FLContext) -> None:
        if event_type != EventType.AFTER_CHECK_CLIENT_RESOURCES:
            return
        try:
            self._report(fl_ctx)
        except Exception as e:
            # This runs inside the scheduler's pass over every queued job; raising would stop that pass.
            self.log_error(fl_ctx, f"Could not report the scheduling verdict to the hub: {e}")

    def _report(self, fl_ctx: FLContext) -> None:
        job_meta = fl_ctx.get_prop(FLContextKey.JOB_META) or {}
        # The fl-api names every job it submits after the hub's model id. That is the one place the id sits in
        # the scheduler's context: the job's config.json is not deployed until the job starts.
        model_id = job_meta.get(JobMetaKey.JOB_NAME)
        if not isinstance(model_id, str) or not Utils.is_valid_uuid(model_id):
            return  # not a job FLIP submitted, so there is no model to report to

        shortfall = scheduling_shortfall(
            job_meta,
            fl_ctx.get_prop(FLContextKey.CLIENT_RESOURCE_SPECS) or {},
            fl_ctx.get_prop(FLContextKey.RESOURCE_CHECK_RESULT) or {},
        )
        if shortfall is None:
            return

        # The scheduler bumps schedule_count after this event, so the meta still holds the earlier tries.
        attempt = int(job_meta.get("schedule_count") or 0) + 1
        max_attempts = self._max_attempts(fl_ctx)
        final = attempt >= max_attempts
        self.log_info(
            fl_ctx,
            f"Job for model {model_id} not scheduled (attempt {attempt} of {max_attempts}): "
            f"sites without the requested resources: {shortfall}",
        )
        progress = {"attempt": attempt, "max_attempts": max_attempts, "final": final}
        # One row per site, sent as that site: the hub resolves the FL client name to its trust and shows
        # the trust beside the row, which a kit-slot name inside the details could not do.
        for site, requested in shortfall.items():
            self.flip.send_event(
                model_id=model_id,
                event_type=FLLogEvent.JOB_WAITING_FOR_RESOURCES,
                global_round=None,
                client_name=site,
                details={**progress, "requested": requested},
                success=not final,
            )
        if not shortfall:
            # Nothing was checked (NVFLARE then logs "resource check results is None or empty"), so no site can
            # be named. One row against the model still says how far the scheduler got; without "requested" the
            # hub does not claim that a trust failed to confirm its resources.
            self.flip.send_event(
                model_id=model_id,
                event_type=FLLogEvent.JOB_WAITING_FOR_RESOURCES,
                global_round=None,
                client_name=None,
                details=progress,
                success=not final,
            )
        if final:
            # The scheduler marks the job FINISHED:CAN_NOT_SCHEDULE on its next pass and fires no event.
            self._fail_model(fl_ctx, model_id)

    def _fail_model(self, fl_ctx: FLContext, model_id: str) -> None:
        for wait in (0, *FINAL_STATUS_RETRY_DELAYS_SECONDS):
            if wait:
                time.sleep(wait)
            if self.flip.update_status(model_id, ModelStatus.ERROR):
                return
        self.log_error(
            fl_ctx,
            f"The hub did not accept model {model_id}'s ERROR status after "
            f"{len(FINAL_STATUS_RETRY_DELAYS_SECONDS) + 1} tries; its net stays BUSY until the model's job is "
            "aborted (Abort job in the UI).",
        )

    def _max_attempts(self, fl_ctx: FLContext) -> int:
        try:
            scheduler = fl_ctx.get_engine().get_component(self.job_scheduler_id)
        except Exception as e:
            # Raising would skip this pass's report, and on the last try the model's ERROR with it.
            self.log_warning(fl_ctx, f"Could not read the job scheduler '{self.job_scheduler_id}': {e}")
            return NVFLARE_DEFAULT_MAX_SCHEDULE_COUNT
        count = getattr(scheduler, "max_schedule_count", None)
        return count if isinstance(count, int) and count > 0 else NVFLARE_DEFAULT_MAX_SCHEDULE_COUNT
