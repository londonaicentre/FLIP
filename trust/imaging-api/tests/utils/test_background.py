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

import pytest

from imaging_api.utils.background import (
    dead_background_tasks,
    reset_dead_background_tasks,
    watch_background_task,
)


@pytest.fixture(autouse=True)
def _clean_record():
    reset_dead_background_tasks()
    yield
    reset_dead_background_tasks()


async def _raises():
    raise RuntimeError("sweeper blew up")


async def _returns():
    return None


async def _runs_until_cancelled():
    await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_task_that_raises_is_recorded():
    task = asyncio.create_task(_raises(), name="image_cache_retention")
    task.add_done_callback(watch_background_task)
    with pytest.raises(RuntimeError):
        await task
    # Done callbacks run via call_soon; yield once so it fires.
    await asyncio.sleep(0)

    assert dead_background_tasks() == {"image_cache_retention"}


@pytest.mark.asyncio
async def test_task_that_returns_unexpectedly_is_recorded():
    task = asyncio.create_task(_returns(), name="image_cache_retention")
    task.add_done_callback(watch_background_task)
    await task
    await asyncio.sleep(0)

    assert dead_background_tasks() == {"image_cache_retention"}


@pytest.mark.asyncio
async def test_cancelled_task_is_not_recorded():
    """Cancellation is the normal shutdown path, not a death."""
    task = asyncio.create_task(_runs_until_cancelled(), name="image_cache_retention")
    task.add_done_callback(watch_background_task)
    await asyncio.sleep(0)  # let it start
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)

    assert dead_background_tasks() == set()


@pytest.mark.asyncio
async def test_reset_clears_the_record():
    task = asyncio.create_task(_raises(), name="image_cache_retention")
    task.add_done_callback(watch_background_task)
    with pytest.raises(RuntimeError):
        await task
    await asyncio.sleep(0)
    assert dead_background_tasks()

    reset_dead_background_tasks()

    assert dead_background_tasks() == set()
