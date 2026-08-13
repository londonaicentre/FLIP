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

"""Liveness bookkeeping for the lifespan's background services.

Both loops run forever by construction, so a finished task means the service is
gone: the process keeps serving HTTP while it has silently stopped polling the hub
or collecting container health. Recording the death lets ``/health`` report
``degraded`` instead of a reassuring "ok".
"""

import asyncio

from trust_api.utils.logger import logger

_dead_tasks: set[str] = set()


def dead_background_tasks() -> set[str]:
    """Return the names of background tasks that have stopped running.

    Returns:
        set[str]: A copy of the recorded names; empty while everything is healthy.
    """
    return set(_dead_tasks)


def reset_dead_background_tasks() -> None:
    """Clear the record (used by tests, and by a fresh lifespan on restart)."""
    _dead_tasks.clear()


def watch_background_task(task: asyncio.Task) -> None:
    """Record and log a background task that finished unexpectedly.

    Cancellation is the normal shutdown path and is ignored. Anything else — a
    raised exception, or an unexpected clean return from a ``while True`` loop —
    means the service is gone for the lifetime of the process.

    Args:
        task (asyncio.Task): The finished background task.
    """
    if task.cancelled():
        return
    exception = task.exception()
    if exception is not None:
        logger.error(f"Background task {task.get_name()!r} died: {type(exception).__name__}: {exception}")
    else:
        logger.error(f"Background task {task.get_name()!r} returned unexpectedly; it should run until shutdown")
    _dead_tasks.add(task.get_name())
