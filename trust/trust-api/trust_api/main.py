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

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from log_config import LoggingMiddleware

from trust_api.config import get_settings
from trust_api.routers.health import router as health_router
from trust_api.services.health_collector import run_health_collector
from trust_api.services.task_poller import run_poller
from trust_api.utils.background import reset_dead_background_tasks, watch_background_task
from trust_api.utils.logger import logger


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Start the task poller and health collector background services.

    Args:
        app (FastAPI): The FastAPI application instance being started.
    """
    reset_dead_background_tasks()
    background_tasks = [
        asyncio.create_task(run_poller(), name="task_poller"),
        asyncio.create_task(run_health_collector(), name="health_collector"),
    ]
    # Both loops run until shutdown, so a finished task means the service is gone:
    # record it so /health stops claiming "ok" while nothing is polling or probing.
    for task in background_tasks:
        task.add_done_callback(watch_background_task)
    logger.info("Trust API started with task poller and health collector")
    yield
    for task in background_tasks:
        task.cancel()
    for task in background_tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass
    logger.info("Trust API shutting down")


# Disable Swagger / OpenAPI / ReDoc in production. Trust services live on a
# Docker-internal network with no inbound ports, but a misconfigured SSM
# port-forward or compose host binding can briefly surface them to a host
# interface — keep the schema unreachable as defence-in-depth.
_docs_enabled = get_settings().ENV != "production"

app = FastAPI(
    title="Trust API",
    description="The entrypoint for the trust. Polls the central hub for tasks and processes them locally.",
    version="0.2.0",
    docs_url="/docs" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    lifespan=lifespan,
)

app.add_middleware(LoggingMiddleware)

# Health endpoint kept for local monitoring (e.g., Docker healthcheck, load balancer)
app.include_router(health_router)

# NOTE: Cohort and imaging routers removed — these are now handled via task polling.
# The trust polls the hub for tasks and processes them using local services.
