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

from fastapi import APIRouter

from trust_api.utils.background import dead_background_tasks
from trust_api.utils.version import service_version

router = APIRouter(prefix="/health", tags=["Health"])


@router.get("")
async def health_check() -> dict[str, object]:
    """
    Health check endpoint for the Trust API

    Reports ``degraded`` when a background service (task poller, health collector)
    has died: the process still answers HTTP, but it has stopped polling the hub
    and/or collecting container health, and a plain "ok" would keep the container
    in rotation while it does nothing.

    Returns:
        dict[str, object]: The service status, its version (the same contract as
        the sibling trust services' /health), and any dead background tasks.
    """
    dead = dead_background_tasks()

    return {
        "status": "degraded" if dead else "ok",
        "version": service_version(),
        "dead_tasks": sorted(dead),
    }
