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

# Ensure structured logging is configured on import
import imaging_api.utils.logger  # noqa: F401
from imaging_api.config import get_settings
from imaging_api.routers.download import router as download_router
from imaging_api.routers.health import router as health_router
from imaging_api.routers.imaging import router as imaging_router
from imaging_api.routers.projects import router as projects_router
from imaging_api.routers.retrieval import router as retrieval_router
from imaging_api.routers.upload import router as upload_router
from imaging_api.routers.users import router as users_router
from imaging_api.services.cache_retention import SWEEP_TASK_NAME, run_cache_retention_sweeper
from imaging_api.utils.background import reset_dead_background_tasks, watch_background_task
from imaging_api.utils.logger import logger


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Start the image-cache retention sweeper background service (FLIP#1050).

    Settings are read here at startup, not at module scope, so tests that reload this
    module with a stubbed settings object don't need the retention fields.

    Args:
        app (FastAPI): The FastAPI application instance being started.
    """
    reset_dead_background_tasks()
    background_tasks: list[asyncio.Task[None]] = []
    if get_settings().IMAGE_CACHE_RETENTION_ENABLED:
        task = asyncio.create_task(run_cache_retention_sweeper(), name=SWEEP_TASK_NAME)
        # The sweeper runs until shutdown, so a finished task means unbounded cache
        # growth has resumed: record it so /health stops claiming "ok".
        task.add_done_callback(watch_background_task)
        background_tasks.append(task)
    else:
        logger.info("Image-cache retention sweeper disabled (IMAGE_CACHE_RETENTION_ENABLED=false)")
    yield
    for task in background_tasks:
        task.cancel()
    for task in background_tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            # A task that died before shutdown re-raises its original exception here.
            # watch_background_task already logged it and flagged /health degraded —
            # shutdown must not crash re-raising a death that was already surfaced.
            pass


# Disable Swagger / OpenAPI / ReDoc in production. Imaging-api proxies privileged
# XNAT operations; leaking its full route + schema map to anyone who reaches the
# port (e.g. via a misconfigured SSM port-forward) is a free recon win.
_docs_enabled = get_settings().ENV != "production"

app = FastAPI(
    title="Imaging API",
    description="An API to interact with XNAT, including creating projects, users, querying from PACS, "
    "downloading and uploading files",
    version="0.1.0",
    docs_url="/docs" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    lifespan=lifespan,
)

app.add_middleware(LoggingMiddleware)

app.include_router(download_router)
app.include_router(health_router)
app.include_router(imaging_router)
app.include_router(projects_router)
app.include_router(retrieval_router)
app.include_router(upload_router)
app.include_router(users_router)
