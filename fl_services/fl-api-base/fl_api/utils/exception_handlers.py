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

from typing import cast

from fastapi import Request
from fastapi.responses import JSONResponse
from nvflare.fuel.flare_api.api_spec import JobNotFound
from starlette.exceptions import HTTPException as StarletteHTTPException


def http_exception_handler(request: Request, exc: Exception):
    e = cast(StarletteHTTPException, exc)
    return JSONResponse(status_code=e.status_code, content={"detail": e.detail})


def bad_request_handler(request: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


def file_not_found_handler(request: Request, exc: FileNotFoundError):
    return JSONResponse(status_code=404, content={"detail": str(exc) or "File not found"})


def job_not_found_handler(request: Request, exc: JobNotFound):
    return JSONResponse(status_code=404, content={"detail": str(exc) or "Job not found"})


def validation_exception_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


def server_error_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"detail": f"Internal server error: {str(exc)}"})
