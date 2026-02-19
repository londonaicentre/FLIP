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

from fastapi import FastAPI
from fl_api.routers import application, health, jobs, system
from fl_api.startup.session_manager import create_fl_session
from fl_api.utils.exception_handlers import bad_request_handler, not_found_handler, server_error_handler
from fl_api.utils.logger import logger

app = FastAPI(
    title="FL API",
    description="API for FLIP FL system",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# Register exception handlers
app.add_exception_handler(400, bad_request_handler)
app.add_exception_handler(404, not_found_handler)
app.add_exception_handler(500, server_error_handler)

# Include routers
app.include_router(health.router, tags=["Health"])
app.include_router(application.router, tags=["Application"])
app.include_router(jobs.router, tags=["Jobs"])
app.include_router(system.router, tags=["System"])


# Startup event to initialize FL session
@app.on_event("startup")
def on_startup():
    """FL API startup event: initializes the FL session."""
    logger.info("Running FL startup initialization...")
    app.state.session = create_fl_session()
    logger.info("FL session initialized successfully.")
    logger.info("FL session initialized successfully.")
