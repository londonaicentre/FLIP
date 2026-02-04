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

from contextlib import asynccontextmanager
from typing import AsyncGenerator

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from flip_api.cohort_services import (
    get_cohort_query_results,
    save_cohort_query,
    submit_cohort_query,
)
from flip_api.file_services import (
    delete_file,
    download_file,
    get_model_files_list,
    presigned_url_for_upload,
    retrieve_federated_results,
    retrieve_model_files_list,
    retrieve_uploaded_file_info,
    uploaded_file,
)
from flip_api.fl_services import (
    get_net_status,
    get_status,
    initiate_training,
    run_jobs,
    stop_training,
)
from flip_api.model_services import (
    delete_model,
    edit_model,
    get_job_types,
    get_metrics,
    retrieve_logs_for_model,
    retrieve_model_status_from_logs,
    retrieve_trusts_in_model,
    save_model,
    update_model_status,
)
from flip_api.private_services import (
    add_log,
    invoke_model_status_update,
    receive_cohort_results,
    save_training_metrics,
)
from flip_api.project_services import (
    approve_project,
    create_project,
    delete_project,
    edit_project,
    get_imaging_project_status,
    get_models,
    get_project,
    get_project_approved_trusts,
    get_projects,
    stage_project,
    unstage_project,
)
from flip_api.role_services import get_roles
from flip_api.scheduler.apscheduler_runner import start_scheduler
from flip_api.site_services import details
from flip_api.step_functions_services import (
    approve_project_step_function,
    cohort_query_step_function,
    register_user_step_function,
    retrieve_model_step_function,
)
from flip_api.trusts_services import (
    get_trusts,
    trusts_health_check,
    update_trust_status,
)
from flip_api.user_services import (
    access_request,
    delete_user,
    get_user,
    get_users,
    register_user,
    retrieve_user_permissions,
    set_user_roles,
    update_user,
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Start scheduler."""
    start_scheduler()
    print("Starting up the app...")
    yield
    print("Shutting down the app...")


# Initialize the FastAPI app
app = FastAPI(title="flip", description="flipflip hub API", version="0.1.0", lifespan=lifespan)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust as needed
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Include routers
# Cohort services
app.include_router(get_cohort_query_results.router)
app.include_router(save_cohort_query.router)
app.include_router(submit_cohort_query.router)
# File services
app.include_router(delete_file.router)
app.include_router(download_file.router)
app.include_router(get_model_files_list.router)
app.include_router(presigned_url_for_upload.router)
app.include_router(retrieve_federated_results.router)
app.include_router(retrieve_model_files_list.router)
app.include_router(retrieve_uploaded_file_info.router)
app.include_router(uploaded_file.router)
# FL services
app.include_router(get_net_status.router)
app.include_router(get_status.router)
app.include_router(initiate_training.router)
app.include_router(run_jobs.router)
app.include_router(stop_training.router)
# Model job types endpoint (moved from FL)
app.include_router(get_job_types.router)
# Model services
app.include_router(delete_model.router)
app.include_router(edit_model.router)
app.include_router(get_metrics.router)
app.include_router(retrieve_logs_for_model.router)
app.include_router(retrieve_model_status_from_logs.router)
app.include_router(retrieve_trusts_in_model.router)
app.include_router(save_model.router)
app.include_router(update_model_status.router)
# Private services
app.include_router(add_log.router)
app.include_router(invoke_model_status_update.router)
app.include_router(receive_cohort_results.router)
app.include_router(save_training_metrics.router)
# Project services
app.include_router(approve_project.router)
app.include_router(create_project.router)
app.include_router(delete_project.router)
app.include_router(edit_project.router)
app.include_router(get_imaging_project_status.router)
app.include_router(get_models.router)
app.include_router(get_project_approved_trusts.router)
app.include_router(get_project.router)
app.include_router(get_projects.router)
app.include_router(stage_project.router)
app.include_router(unstage_project.router)
# Roles services
app.include_router(get_roles.router)
# Site services
app.include_router(details.router)
# Step functions services
app.include_router(approve_project_step_function.router)
app.include_router(cohort_query_step_function.router)
app.include_router(register_user_step_function.router)
app.include_router(retrieve_model_step_function.router)
# Trust services
app.include_router(get_trusts.router)
app.include_router(trusts_health_check.router)
app.include_router(update_trust_status.router)
# User services
app.include_router(access_request.router)
app.include_router(delete_user.router)
app.include_router(get_user.router)
app.include_router(get_users.router)
app.include_router(register_user.router)
app.include_router(retrieve_user_permissions.router)
app.include_router(set_user_roles.router)
app.include_router(update_user.router)


# Root endpoint
@app.get("/")
def root():
    return {"message": "Welcome to flip"}


@app.get("/health")
def health_check():
    """Health check endpoint to verify the API is running"""
    return {"status": "ok", "message": "flip is running"}


def main():
    """Entry point for the application script"""
    uvicorn.run("flip_api.main:app", host="0.0.0.0", port=81, reload=True)


if __name__ == "__main__":
    main()
