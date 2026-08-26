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

"""TTL retention sweeper for the on-disk image download cache (FLIP#1050).

The cache under ``BASE_IMAGES_DOWNLOAD_DIR`` (layout and completeness contract in
``services/image_cache.py``) has no other bound: NVFLARE's app-level ``CleanupImages``
wipes a net dir at job start/end, but Flower has no site-side hook, so without this
sweeper every project ever run on a Flower net leaves its imaging on the trust host
forever. The sweep removes an accession directory once its last use — the newest
``.flip_complete-*`` sentinel mtime, refreshed on every cache hit — is older than the
TTL, plus crash-orphaned staging zips and emptied project dirs.

Concurrency invariant (load-bearing, do not relax): ``sweep_expired_cache_entries`` is
fully synchronous — no ``await`` from first stat to last delete — and imaging-api runs
one uvicorn worker (see entrypoint.sh), so a pass runs only *between* requests on the
shared event loop and can never interleave with ``download_and_unzip_images`` (itself
await-free — see the matching invariant comment there and FLIP#1026). That is why no
locking exists here; offloading the sweep to a thread or executor, or awaiting
mid-pass, reintroduces the race with in-flight extractions. The deliberate cost is
that a pass blocks the loop for its duration — acceptable because the download path's
synchronous ``requests`` transfers already block it for far longer.

Deletions still tolerate concurrent removal (ENOENT is benign): NVFLARE's
``CleanupImages`` empties net-dir children from the fl-client container, invisible to
this process's serialisation.
"""

import asyncio
import os
import shutil
import time
from dataclasses import dataclass

from imaging_api.config import get_settings
from imaging_api.services.image_cache import SENTINEL_PREFIX
from imaging_api.utils.logger import logger

# Name surfaced by /health's dead_tasks when the sweeper dies.
SWEEP_TASK_NAME = "image_cache_retention"

# Upload staging dir (services/upload.py) — a sibling of the per-project cache dirs
# inside each net dir. Never swept, however old; the unconditional name skip also
# guards against a central-hub project literally named "upload".
UPLOAD_DIR_NAME = "upload"

# Module snapshot, sibling pattern to download.py/upload.py: tests patch this name.
BASE_IMAGES_DOWNLOAD_DIR = get_settings().BASE_IMAGES_DOWNLOAD_DIR


@dataclass
class SweepStats:
    """Counters for one retention sweep pass."""

    accession_dirs_removed: int = 0
    orphan_zips_removed: int = 0
    empty_project_dirs_removed: int = 0
    bytes_freed: int = 0
    errors: int = 0


def _entry_last_used(accession_dir_abs: str) -> float:
    """
    Returns the last-used time of one cached accession directory.

    The newest sentinel mtime governs: several (assessor, resource) sentinels share the
    directory's bytes, so the bytes are only expired once *every* completeness claim on
    them has aged out. A sentinel-less directory (invalidated by ``invalidate_accession``,
    or a partial/crashed extraction) falls back to the directory's own mtime as a grace
    clock — it restarts whenever content changes, which only ever delays reaping.

    Args:
        accession_dir_abs (str): Absolute accession directory to inspect.

    Returns:
        float: POSIX timestamp of the entry's last use.
    """
    newest = None
    with os.scandir(accession_dir_abs) as entries:
        for entry in entries:
            if entry.name.startswith(SENTINEL_PREFIX) and entry.is_file(follow_symlinks=False):
                mtime = entry.stat(follow_symlinks=False).st_mtime
                if newest is None or mtime > newest:
                    newest = mtime
    if newest is not None:
        return newest
    return os.stat(accession_dir_abs).st_mtime


def _dir_size_bytes(dir_abs: str) -> int:
    """
    Returns the total size of the regular files under a directory (symlinks not followed).

    Args:
        dir_abs (str): Absolute directory to measure.

    Returns:
        int: Sum of file sizes in bytes; best-effort (races with deletion are tolerated).
    """
    total = 0
    for dirpath, _dirnames, filenames in os.walk(dir_abs, followlinks=False):
        for filename in filenames:
            try:
                total += os.lstat(os.path.join(dirpath, filename)).st_size
            except OSError:
                continue
    return total


def _remove_expired_accession_dir(accession_dir: str, base_abs: str, stats: SweepStats) -> None:
    """
    Removes one expired accession directory, counting bytes freed and tolerating races.

    Args:
        accession_dir (str): The accession directory to remove.
        base_abs (str): Realpath of the cache base dir; deletion is refused outside it.
        stats (SweepStats): Counters to update in place.
    """
    accession_dir_real = os.path.realpath(accession_dir)
    # Belt-and-braces, mirroring invalidate_accession: never delete outside the base dir.
    if not accession_dir_real.startswith(base_abs + os.sep):
        return
    size = _dir_size_bytes(accession_dir_real)

    def _onexc(_func: object, _path: str, exc: BaseException) -> None:
        # ENOENT is benign — CleanupImages may be emptying this net dir concurrently
        # from the fl-client container.
        if isinstance(exc, FileNotFoundError):
            return
        stats.errors += 1
        logger.error(f"Cache retention: could not remove {_path!r}: {type(exc).__name__}: {exc}")

    shutil.rmtree(accession_dir_real, onexc=_onexc)
    if not os.path.exists(accession_dir_real):
        stats.accession_dirs_removed += 1
        stats.bytes_freed += size


def _sweep_project_dir(project_dir: str, base_abs: str, cutoff: float, stats: SweepStats) -> None:
    """
    Sweeps one per-project cache directory: expired accession dirs, crash-orphaned staging
    zips (``download_and_unzip_images`` stages ``<accession>-scans-<resource>.zip`` here and
    deletes it after extraction — one older than the TTL was left by a crash), then the
    project dir itself if empty.

    Args:
        project_dir (str): The project directory (child of a net dir).
        base_abs (str): Realpath of the cache base dir.
        cutoff (float): POSIX timestamp; entries last used before it are expired.
        stats (SweepStats): Counters to update in place.
    """
    with os.scandir(project_dir) as scan:
        entries = list(scan)
    for entry in entries:
        try:
            if entry.is_symlink():
                continue
            if entry.is_dir(follow_symlinks=False):
                if _entry_last_used(entry.path) < cutoff:
                    _remove_expired_accession_dir(entry.path, base_abs, stats)
            elif entry.is_file(follow_symlinks=False) and entry.name.endswith(".zip"):
                if entry.stat(follow_symlinks=False).st_mtime < cutoff:
                    entry_real = os.path.realpath(entry.path)
                    if entry_real.startswith(base_abs + os.sep):
                        size = os.lstat(entry_real).st_size
                        os.unlink(entry_real)
                        stats.orphan_zips_removed += 1
                        stats.bytes_freed += size
        except OSError as e:
            stats.errors += 1
            logger.error(f"Cache retention: failed sweeping {entry.path!r}: {type(e).__name__}: {e}")
    try:
        os.rmdir(project_dir)  # only succeeds when empty; ENOTEMPTY keeps it
        stats.empty_project_dirs_removed += 1
    except OSError:
        pass


def sweep_expired_cache_entries(base_dir: str, ttl_seconds: float, now: float | None = None) -> SweepStats:
    """
    Runs one synchronous retention pass over the whole image download cache.

    Walks ``<base>/<net>/<project>/<accession>``, removing accession dirs whose last use
    (see ``_entry_last_used``) predates the TTL, project-level ``*.zip`` staging files
    orphaned by a crash, and project dirs left empty. Net dirs are pre-created mount
    points with backend-aware ownership and are never removed — nor is the ``upload``
    staging dir inside each net. Symlinks are never followed or deleted through. Every
    per-item failure is counted and logged without aborting the pass.

    Args:
        base_dir (str): The base images download directory (one subdirectory per net).
        ttl_seconds (float): Seconds since last use after which an entry is expired.
        now (float | None): Injectable clock for tests; defaults to ``time.time()``.

    Returns:
        SweepStats: What the pass removed and how many item-level errors it hit.
    """
    stats = SweepStats()
    base_abs = os.path.realpath(base_dir)
    if not os.path.isdir(base_abs):
        logger.warning(f"Cache retention: base images dir {base_dir!r} does not exist; nothing to sweep")
        return stats
    cutoff = (now if now is not None else time.time()) - ttl_seconds

    with os.scandir(base_abs) as entries:
        net_dirs = [e.path for e in entries if e.is_dir(follow_symlinks=False)]
    for net_dir in net_dirs:
        try:
            with os.scandir(net_dir) as entries:
                project_entries = list(entries)
        except OSError as e:
            stats.errors += 1
            logger.error(f"Cache retention: cannot list net dir {net_dir!r}: {type(e).__name__}: {e}")
            continue
        for entry in project_entries:
            try:
                if entry.name == UPLOAD_DIR_NAME or entry.is_symlink():
                    continue
                # Stray files directly under a net dir are not ours to manage — the
                # cache only ever writes project *directories* here.
                if entry.is_dir(follow_symlinks=False):
                    _sweep_project_dir(entry.path, base_abs, cutoff, stats)
            except OSError as e:
                stats.errors += 1
                logger.error(f"Cache retention: failed sweeping {entry.path!r}: {type(e).__name__}: {e}")
    return stats


async def run_cache_retention_sweeper() -> None:
    """Runs the retention sweep forever at the configured interval (sweep first, then sleep).

    The first pass at startup reaps any backlog accumulated while the service was down.
    A failing pass is logged and the loop survives — only cancellation (shutdown) ends it.
    """
    settings = get_settings()
    ttl_seconds = settings.IMAGE_CACHE_RETENTION_HOURS * 3600
    interval_seconds = max(settings.IMAGE_CACHE_SWEEP_INTERVAL_MINUTES, 1.0) * 60
    logger.info(
        f"Image-cache retention sweeper started: ttl={settings.IMAGE_CACHE_RETENTION_HOURS}h "
        f"interval={settings.IMAGE_CACHE_SWEEP_INTERVAL_MINUTES}m base={BASE_IMAGES_DOWNLOAD_DIR!r}"
    )
    if settings.IMAGE_CACHE_RETENTION_HOURS < 24:
        logger.warning(
            f"IMAGE_CACHE_RETENTION_HOURS={settings.IMAGE_CACHE_RETENTION_HOURS} is below 24h: an FL job "
            "running longer than the TTL risks its images being swept mid-job (NVFLARE clients refresh "
            "last-used only at job start)"
        )
    while True:
        started = time.monotonic()
        try:
            stats = sweep_expired_cache_entries(BASE_IMAGES_DOWNLOAD_DIR, ttl_seconds)
            logger.info(
                f"Cache retention sweep: {stats.accession_dirs_removed} accession dirs, "
                f"{stats.orphan_zips_removed} orphaned zips, {stats.empty_project_dirs_removed} empty "
                f"project dirs removed, {stats.bytes_freed / 1e9:.2f} GB freed, {stats.errors} errors, "
                f"{time.monotonic() - started:.1f}s"
            )
        except Exception:
            logger.exception("Cache retention sweep pass failed")
        await asyncio.sleep(interval_seconds)
