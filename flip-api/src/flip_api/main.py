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
from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from flip_api.cohort_services import (
    get_cohort_query_results,
    save_cohort_query,
    submit_cohort_query,
)
from flip_api.config import get_settings
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


API_PREFIX = "/api"
docs_enabled = get_settings().ENV != "production"

# Initialize the FastAPI app
app = FastAPI(
    title="FLIP CentralHub API",
    description="Main API for FLIP CentralHub, providing communication between the frontend and backend services.",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=f"{API_PREFIX}/docs" if docs_enabled else None,
    openapi_url=f"{API_PREFIX}/openapi.json" if docs_enabled else None,
    redoc_url=f"{API_PREFIX}/redoc" if docs_enabled else None,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust as needed
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


ROUTERS: tuple[APIRouter, ...] = (
    # Cohort services
    get_cohort_query_results.router,
    save_cohort_query.router,
    submit_cohort_query.router,
    # File services
    delete_file.router,
    download_file.router,
    get_model_files_list.router,
    presigned_url_for_upload.router,
    retrieve_federated_results.router,
    retrieve_model_files_list.router,
    retrieve_uploaded_file_info.router,
    uploaded_file.router,
    # FL services
    get_net_status.router,
    get_status.router,
    initiate_training.router,
    run_jobs.router,
    stop_training.router,
    # Model job types endpoint (moved from FL)
    get_job_types.router,
    # Model services
    delete_model.router,
    edit_model.router,
    get_metrics.router,
    retrieve_logs_for_model.router,
    retrieve_model_status_from_logs.router,
    retrieve_trusts_in_model.router,
    save_model.router,
    update_model_status.router,
    # Private services
    add_log.router,
    invoke_model_status_update.router,
    receive_cohort_results.router,
    save_training_metrics.router,
    # Project services
    approve_project.router,
    create_project.router,
    delete_project.router,
    edit_project.router,
    get_imaging_project_status.router,
    get_models.router,
    get_project_approved_trusts.router,
    get_project.router,
    get_projects.router,
    stage_project.router,
    unstage_project.router,
    # Roles services
    get_roles.router,
    # Site services
    details.router,
    # Step functions services
    approve_project_step_function.router,
    cohort_query_step_function.router,
    register_user_step_function.router,
    retrieve_model_step_function.router,
    # Trust services
    get_trusts.router,
    trusts_health_check.router,
    update_trust_status.router,
    # User services
    access_request.router,
    delete_user.router,
    get_user.router,
    get_users.router,
    register_user.router,
    retrieve_user_permissions.router,
    set_user_roles.router,
    update_user.router,
)


def include_api_routers(fastapi_app: FastAPI) -> None:
    """Mount all API routes under the shared /api namespace."""
    for router in ROUTERS:
        fastapi_app.include_router(router, prefix=API_PREFIX)


include_api_routers(app)


# Root endpoint
@app.get(API_PREFIX, response_model=dict[str, str])
def root():
    """Root endpoint to verify the API is running."""
    return {"message": "Welcome to flip"}


@app.get(f"{API_PREFIX}/health", response_model=dict[str, str])
def health_check():
    """Health check endpoint to verify the API is running"""
    return {"status": "ok", "message": "flip is running"}


def main():
    """Entry point for the application script"""
    uvicorn.run("flip_api.main:app", host="0.0.0.0", port=81, reload=True)


if __name__ == "__main__":
    main()
