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

"""Rate limiter configuration for trust-facing API endpoints."""

from fastapi import Request
from slowapi import Limiter


def _trust_name_key(request: Request) -> str:
    """Extract trust_name from path params for per-trust rate limiting.

    Args:
        request (Request): The incoming FastAPI request.

    Returns:
        str: The ``trust_name`` path parameter when present; otherwise the client's host; otherwise
        the literal ``"unknown"``.
    """
    return request.path_params.get("trust_name", request.client.host if request.client else "unknown")


limiter = Limiter(key_func=_trust_name_key)
