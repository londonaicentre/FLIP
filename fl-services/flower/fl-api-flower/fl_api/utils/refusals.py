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

from typing import cast

from fastapi import Request
from fastapi.responses import JSONResponse


class ResearcherRefusal(ValueError):
    """An intentional refusal whose message is safe to show in a researcher's activity feed."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def researcher_refusal_handler(request: Request, exc: Exception) -> JSONResponse:
    refusal = cast(ResearcherRefusal, exc)
    return JSONResponse(status_code=refusal.status_code, content={"detail": str(refusal), "refusal": str(refusal)})
