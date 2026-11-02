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

"""Removes cached studies unused for IMAGE_CACHE_RETENTION_HOURS (FLIP#1050).

Only NVFLARE's ``CleanupImages`` deletes cached studies, and only where the fl-client mounts the net folder, so
without this every project ever run on a Flower net leaves its imaging on the trust host.
"""

import asyncio
import os
import shutil
import time

from imaging_api.config import get_settings
from imaging_api.services.image_cache import SENTINEL_PREFIX
from imaging_api.utils.logger import logger

SWEEP_INTERVAL_SECONDS = 3600
UPLOAD_DIR_NAME = "upload"  # the upload staging dir inside each net dir, never swept

# Module snapshot, as in download.py: tests patch this name.
BASE_IMAGES_DOWNLOAD_DIR = get_settings().BASE_IMAGES_DOWNLOAD_DIR


def _subdirs(path: str) -> list[str]:
    try:
        with os.scandir(path) as entries:
            return [entry.path for entry in entries if entry.is_dir(follow_symlinks=False)]
    except FileNotFoundError:
        return []


def _last_used(accession_dir: str) -> float:
    """The newest sentinel's mtime (refreshed on every cache hit), or the directory's own when it has none.

    The directory's mtime also counts: it moves whenever files land in it, so a half-extracted study is never expired.
    """
    with os.scandir(accession_dir) as entries:
        times = [e.stat().st_mtime for e in entries if e.name.startswith(SENTINEL_PREFIX) and e.is_file()]
    return max([os.stat(accession_dir).st_mtime, *times])


async def sweep(base_dir: str, ttl_seconds: float, now: float | None = None) -> int:
    """Remove every ``<net>/<project>/<accession>`` directory unused for ``ttl_seconds``; returns how many.

    Runs on the event loop and yields only between studies: a download or a cache hit, which run on the same loop
    without awaiting, can never land between a study's check and its removal.
    """
    cutoff = (time.time() if now is None else now) - ttl_seconds
    removed = 0
    for net_dir in _subdirs(base_dir):
        for project_dir in _subdirs(net_dir):
            if os.path.basename(project_dir) == UPLOAD_DIR_NAME:
                continue
            for accession_dir in _subdirs(project_dir):
                try:
                    if _last_used(accession_dir) < cutoff:
                        shutil.rmtree(accession_dir)
                        removed += 1
                except OSError as e:
                    logger.warning(f"Image-cache sweep could not remove {accession_dir}: {e}")
                await asyncio.sleep(0)
    return removed


async def run_sweeper() -> None:
    """Sweep hourly for the app's lifetime."""
    ttl_seconds = get_settings().IMAGE_CACHE_RETENTION_HOURS * 3600
    while True:
        try:
            removed = await sweep(BASE_IMAGES_DOWNLOAD_DIR, ttl_seconds)
            if removed:
                logger.info(f"Image-cache sweep removed {removed} unused cached stud{'y' if removed == 1 else 'ies'}")
        except Exception:
            logger.exception("Image-cache sweep failed")
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
