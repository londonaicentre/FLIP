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
)

app.add_middleware(LoggingMiddleware)

app.include_router(download_router)
app.include_router(health_router)
app.include_router(imaging_router)
app.include_router(projects_router)
app.include_router(retrieval_router)
app.include_router(upload_router)
app.include_router(users_router)
