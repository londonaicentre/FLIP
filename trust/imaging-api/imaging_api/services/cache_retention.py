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
``services/image_cache.py``) has no other bound: nothing else on either FL backend
deletes downloaded imaging (the NVFLARE ``CleanupImages`` executor that wiped the net
dir at job start/end is retired — it defeated the cache between runs — and Flower
never had a site-side hook), so without this sweeper every project ever run on a net
leaves its imaging on the trust host forever. The sweep removes an accession directory
once its last use — the newest ``.flip_complete-*`` sentinel mtime, refreshed on every
cache hit — is older than the TTL, plus crash-orphaned staging zips and emptied
project dirs.

Concurrency invariant (load-bearing, do not relax): each swept item's expiry check and
removal run **without yielding** — ``_sweep_steps`` is a generator whose step bodies
contain no ``await``, and imaging-api runs one uvicorn worker (see entrypoint.sh) — so
a deletion can never interleave with ``download_and_unzip_images`` (itself await-free —
see the matching invariant comment there and FLIP#1026). That per-item atomicity is why
no locking exists here; offloading step bodies to a thread/executor, or awaiting inside
one, reintroduces the race with in-flight extractions. *Between* items the driving loop
yields to the event loop, so a mass expiry cannot starve ``/health`` (K8s liveness
probes) or queued downloads for the length of a whole pass — the blocking unit is one
accession directory's stat+rmtree, not the pass. A download that lands between items is
safe by construction: a fresh extraction keeps its directory mtime (and then sentinel)
inside the TTL, so the next step skips it.

Deletions tolerate concurrent removal (``FileNotFoundError`` is benign at every level,
skipped without counting as an error): another actor — an operator tidying by hand, or
an outdated fl-client image still running the retired ``CleanupImages`` wipe — can
empty net-dir children invisible to this process's serialisation.
"""

import asyncio
import os
import shutil
import time
from collections.abc import Iterator
from dataclasses import dataclass

from imaging_api.config import get_settings
from imaging_api.services.image_cache import SENTINEL_PREFIX
from imaging_api.utils.background import (
    clear_degraded_background_service,
    record_degraded_background_service,
)
from imaging_api.utils.logger import logger

# Name surfaced by /health's dead_tasks when the sweeper dies.
SWEEP_TASK_NAME = "image_cache_retention"

# Name surfaced by /health when the sweeper is alive but its passes keep failing —
# without this, a permanently-erroring sweep (e.g. an undeletable cache after a uid
# change) would fill the disk for months with nothing but hourly log lines to show.
SWEEP_FAILING_HEALTH_NAME = "image_cache_retention:failing"

# Consecutive failing passes (item errors, or a pass-level exception) before /health
# degrades; one clean pass clears it.
FAILING_PASS_THRESHOLD = 3

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
    # This branch should be unreachable (symlinks are filtered upstream) — which is
    # exactly why firing it must leave a trace, not a silently-immortal cache entry.
    if not accession_dir_real.startswith(base_abs + os.sep):
        stats.errors += 1
        logger.error(
            f"Cache retention: refusing to remove {accession_dir!r} — resolves to "
            f"{accession_dir_real!r}, outside the cache base dir"
        )
        return
    size = _dir_size_bytes(accession_dir_real)

    def _onexc(_func: object, _path: str, exc: BaseException) -> None:
        # ENOENT is benign — another actor (an operator, or an outdated fl-client image
        # still running the retired CleanupImages wipe) may empty this dir concurrently.
        if isinstance(exc, FileNotFoundError):
            return
        stats.errors += 1
        logger.error(f"Cache retention: could not remove {_path!r}: {type(exc).__name__}: {exc}")

    shutil.rmtree(accession_dir_real, onexc=_onexc)
    if not os.path.exists(accession_dir_real):
        stats.accession_dirs_removed += 1
        stats.bytes_freed += size


def _sweep_project_dir(project_dir: str, base_abs: str, cutoff: float, stats: SweepStats) -> Iterator[None]:
    """
    Sweeps one per-project cache directory, yielding once per handled entry.

    Removes expired accession dirs and crash-orphaned staging zips
    (``download_and_unzip_images`` stages ``<accession>-scans-<resource>.zip`` here and
    deletes it after extraction — one older than the TTL was left by a crash), then the
    project dir itself if empty. Each yield lands *after* the entry's check+removal
    completed — the caller may suspend at the yield without breaking per-item atomicity.

    Args:
        project_dir (str): The project directory (child of a net dir).
        base_abs (str): Realpath of the cache base dir.
        cutoff (float): POSIX timestamp; entries last used before it are expired.
        stats (SweepStats): Counters to update in place.
    """
    try:
        with os.scandir(project_dir) as scan:
            entries = list(scan)
    except FileNotFoundError:
        return  # concurrently removed — benign
    except OSError as e:
        stats.errors += 1
        logger.error(f"Cache retention: cannot list project dir {project_dir!r}: {type(e).__name__}: {e}")
        return
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
        except FileNotFoundError:
            continue  # concurrently removed under us — benign
        except OSError as e:
            stats.errors += 1
            logger.error(f"Cache retention: failed sweeping {entry.path!r}: {type(e).__name__}: {e}")
        yield
    try:
        os.rmdir(project_dir)  # only succeeds when empty; ENOTEMPTY keeps it
        stats.empty_project_dirs_removed += 1
    except OSError:
        pass


def _sweep_steps(base_dir: str, ttl_seconds: float, now: float, stats: SweepStats) -> Iterator[None]:
    """
    Generator running one retention pass, yielding between handled items.

    The driving loop chooses whether to yield control (``run_cache_retention_sweeper``
    awaits between items; ``sweep_expired_cache_entries`` drains synchronously). Net
    dirs are pre-created mount points with backend-aware ownership and are never
    removed — nor is the ``upload`` staging dir inside each net. Symlinks are never
    followed or deleted through. Item-level failures are counted and logged without
    aborting the pass; concurrent removals (``FileNotFoundError``) are skipped silently.

    Args:
        base_dir (str): The base images download directory (one subdirectory per net).
        ttl_seconds (float): Seconds since last use after which an entry is expired.
        now (float): POSIX timestamp to measure the TTL against.
        stats (SweepStats): Counters to update in place.
    """
    base_abs = os.path.realpath(base_dir)
    if not os.path.isdir(base_abs):
        logger.warning(f"Cache retention: base images dir {base_dir!r} does not exist; nothing to sweep")
        return
    cutoff = now - ttl_seconds

    with os.scandir(base_abs) as entries:
        net_dirs = [e.path for e in entries if e.is_dir(follow_symlinks=False)]
    for net_dir in net_dirs:
        try:
            with os.scandir(net_dir) as entries:
                project_entries = list(entries)
        except FileNotFoundError:
            continue  # concurrently removed — benign
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
                    yield from _sweep_project_dir(entry.path, base_abs, cutoff, stats)
            except FileNotFoundError:
                continue
            except OSError as e:
                stats.errors += 1
                logger.error(f"Cache retention: failed sweeping {entry.path!r}: {type(e).__name__}: {e}")


def sweep_expired_cache_entries(base_dir: str, ttl_seconds: float, now: float | None = None) -> SweepStats:
    """
    Runs one full retention pass synchronously (drains ``_sweep_steps``).

    Args:
        base_dir (str): The base images download directory (one subdirectory per net).
        ttl_seconds (float): Seconds since last use after which an entry is expired.
        now (float | None): Injectable clock for tests; defaults to ``time.time()``.

    Returns:
        SweepStats: What the pass removed and how many item-level errors it hit.
    """
    stats = SweepStats()
    for _ in _sweep_steps(base_dir, ttl_seconds, now if now is not None else time.time(), stats):
        pass
    return stats


async def run_cache_retention_sweeper() -> None:
    """Runs the retention sweep forever at the configured interval (sweep first, then sleep).

    The first pass at startup reaps any backlog accumulated while the service was down.
    A failing pass is logged and the loop survives — only cancellation (shutdown) ends
    it — but ``FAILING_PASS_THRESHOLD`` consecutive failing passes mark
    ``SWEEP_FAILING_HEALTH_NAME`` into the background-service registry so ``/health``
    reports ``degraded`` instead of an endless stream of unread error logs; one clean
    pass clears it.
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
    failing_streak = 0
    while True:
        started = time.monotonic()
        stats = SweepStats()
        pass_failed = False
        try:
            for _ in _sweep_steps(BASE_IMAGES_DOWNLOAD_DIR, ttl_seconds, time.time(), stats):
                # Yield between items so a mass expiry can't starve /health or queued
                # downloads; each item's check+delete completed before this point (the
                # per-item atomicity invariant in the module docstring).
                await asyncio.sleep(0)
            logger.info(
                f"Cache retention sweep: {stats.accession_dirs_removed} accession dirs, "
                f"{stats.orphan_zips_removed} orphaned zips, {stats.empty_project_dirs_removed} empty "
                f"project dirs removed, {stats.bytes_freed / 1e9:.2f} GB freed, {stats.errors} errors, "
                f"{time.monotonic() - started:.1f}s"
            )
        except Exception:
            logger.exception("Cache retention sweep pass failed")
            pass_failed = True
        if pass_failed or stats.errors:
            failing_streak += 1
            if failing_streak == FAILING_PASS_THRESHOLD:
                record_degraded_background_service(SWEEP_FAILING_HEALTH_NAME)
                logger.error(
                    f"Cache retention has now failed {failing_streak} consecutive passes — "
                    f"marking {SWEEP_FAILING_HEALTH_NAME!r} degraded on /health (cache growth "
                    "is effectively unbounded until this recovers)"
                )
        else:
            if failing_streak >= FAILING_PASS_THRESHOLD:
                logger.info("Cache retention recovered after a clean pass; clearing degraded marker")
            failing_streak = 0
            clear_degraded_background_service(SWEEP_FAILING_HEALTH_NAME)
        await asyncio.sleep(interval_seconds)
