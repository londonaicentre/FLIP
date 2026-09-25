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

from datetime import timedelta

from flip_api.config import get_settings
from flip_api.fl_services.reconcile_failed_jobs import reconcile_failed_fl_jobs_scheduled_task
from flip_api.scheduler import apscheduler_runner


def test_fl_job_reconcile_is_registered_at_its_configured_rate():
    # The whole FLIP#1001 fix hangs off this one registration: drop it and every test of the
    # sweep still passes while no run is ever reconciled.
    jobs = [
        job for job in apscheduler_runner.scheduler.get_jobs() if job.func is reconcile_failed_fl_jobs_scheduled_task
    ]

    assert len(jobs) == 1
    assert jobs[0].trigger.interval == timedelta(minutes=get_settings().SCHEDULER_FL_JOB_RECONCILE_RATE)
