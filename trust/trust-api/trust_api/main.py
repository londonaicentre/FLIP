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

from trust_api.routers.health import router as health_router
from trust_api.services.task_poller import run_poller
from trust_api.utils.logger import logger


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Start the task poller background service."""
    poller_task = asyncio.create_task(run_poller())
    logger.info("Trust API started with task poller")
    yield
    poller_task.cancel()
    try:
        await poller_task
    except asyncio.CancelledError:
        pass
    logger.info("Trust API shutting down")


app = FastAPI(
    title="Trust API",
    description="The entrypoint for the trust. Polls the central hub for tasks and processes them locally.",
    version="0.2.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(LoggingMiddleware)

# Health endpoint kept for local monitoring (e.g., Docker healthcheck, load balancer)
app.include_router(health_router)

# NOTE: Cohort and imaging routers removed — these are now handled via task polling.
# The trust polls the hub for tasks and processes them using local services.
