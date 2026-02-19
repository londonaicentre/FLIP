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

from fastapi import Request
from fastapi.responses import JSONResponse


def server_error_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"error": str(exc)})


def bad_request_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=400, content={"error": str(exc)})


def not_found_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=404, content={"error": str(exc)})
