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

from apscheduler.schedulers.background import BackgroundScheduler

from flip_api.config import get_settings
from flip_api.fl_services.run_jobs import run_jobs_scheduled_task
from flip_api.fl_services.services.fl_service import keep_fl_api_session_alive
from flip_api.project_services.reimport_imaging_project_studies import (
    reimport_imaging_project_studies_scheduled_task,
)

# Periodic functions to run at set intervals
scheduler = BackgroundScheduler()

if get_settings().SCHEDULE_RUN_JOBS_EXECUTION:
    scheduler.add_job(
        run_jobs_scheduled_task,
        "interval",
        minutes=get_settings().SCHEDULER_RUN_JOBS_RATE,
    )

scheduler.add_job(
    keep_fl_api_session_alive,
    "interval",
    minutes=get_settings().SCHEDULER_KEEP_FL_API_SESSION_ALIVE_RATE,
)
scheduler.add_job(
    reimport_imaging_project_studies_scheduled_task,
    "interval",
    minutes=get_settings().SCHEDULER_REIMPORT_IMAGING_PROJECT_STUDIES_RATE,
)


def start_scheduler():
    scheduler.start()
