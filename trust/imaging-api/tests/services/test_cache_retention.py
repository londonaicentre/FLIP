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
import os
import shutil
import time
from unittest.mock import MagicMock, patch

import pytest

from imaging_api.services import cache_retention
from imaging_api.services.cache_retention import SweepStats, sweep_expired_cache_entries
from imaging_api.services.image_cache import SENTINEL_PREFIX, write_sentinel

TTL = 3600.0
NOW = 1_700_000_000.0
FRESH = NOW - 60  # well inside the TTL
EXPIRED = NOW - TTL - 60  # just past it


def _age(path, mtime):
    os.utime(path, (mtime, mtime))


def _seed_accession(base, net, project, accession, *, sentinel_mtimes=(), dir_mtime=None, payload_bytes=100):
    """Create one cached accession dir with a payload file and 0+ aged sentinels."""
    accession_dir = os.path.join(str(base), net, project, accession)
    os.makedirs(accession_dir, exist_ok=True)
    payload = os.path.join(accession_dir, "scans", "1", "resources", "NIFTI", "files")
    os.makedirs(payload, exist_ok=True)
    with open(os.path.join(payload, "input_1.nii.gz"), "wb") as fh:
        fh.write(b"x" * payload_bytes)
    for i, mtime in enumerate(sentinel_mtimes):
        resource = f"RES{i}"
        write_sentinel(accession_dir, "scan", resource)
        _age(os.path.join(accession_dir, f"{SENTINEL_PREFIX}scan-{resource}"), mtime)
    if dir_mtime is not None:
        _age(accession_dir, dir_mtime)
    return accession_dir


class TestSweepExpiry:
    def test_expired_accession_dir_removed_fresh_kept(self, tmp_path):
        expired = _seed_accession(tmp_path, "net-1", "proj-a", "ACC_OLD", sentinel_mtimes=[EXPIRED])
        fresh = _seed_accession(tmp_path, "net-1", "proj-a", "ACC_NEW", sentinel_mtimes=[FRESH])

        stats = sweep_expired_cache_entries(str(tmp_path), TTL, now=NOW)

        assert not os.path.exists(expired)
        assert os.path.isdir(fresh)
        assert stats.accession_dirs_removed == 1
        assert stats.bytes_freed >= 100

    def test_newest_of_multiple_sentinels_governs(self, tmp_path):
        """Several (assessor, resource) sentinels share the bytes: one fresh claim keeps them."""
        kept = _seed_accession(tmp_path, "net-1", "proj-a", "ACC1", sentinel_mtimes=[EXPIRED, FRESH])

        stats = sweep_expired_cache_entries(str(tmp_path), TTL, now=NOW)

        assert os.path.isdir(kept)
        assert stats.accession_dirs_removed == 0

    def test_sentinel_less_dir_reaped_on_dir_mtime_grace(self, tmp_path):
        """Invalidated (sentinels unlinked) or partially-extracted dirs fall back to dir mtime."""
        stale = _seed_accession(tmp_path, "net-1", "proj-a", "ACC_STALE", dir_mtime=EXPIRED)
        recent = _seed_accession(tmp_path, "net-1", "proj-a", "ACC_RECENT", dir_mtime=FRESH)

        stats = sweep_expired_cache_entries(str(tmp_path), TTL, now=NOW)

        assert not os.path.exists(stale)
        assert os.path.isdir(recent)
        assert stats.accession_dirs_removed == 1


class TestSweepNeverTouches:
    def test_upload_staging_dir_survives_however_old(self, tmp_path):
        upload_dir = os.path.join(str(tmp_path), "net-1", "upload")
        os.makedirs(upload_dir)
        staged = os.path.join(upload_dir, "pending.zip")
        with open(staged, "wb") as fh:
            fh.write(b"x")
        _age(staged, EXPIRED - 1_000_000)
        _age(upload_dir, EXPIRED - 1_000_000)

        stats = sweep_expired_cache_entries(str(tmp_path), TTL, now=NOW)

        assert os.path.isfile(staged)
        assert stats.accession_dirs_removed == 0
        assert stats.orphan_zips_removed == 0

    def test_net_and_base_dirs_survive_even_when_emptied(self, tmp_path):
        _seed_accession(tmp_path, "net-1", "proj-a", "ACC1", sentinel_mtimes=[EXPIRED])

        sweep_expired_cache_entries(str(tmp_path), TTL, now=NOW)

        assert os.path.isdir(os.path.join(str(tmp_path), "net-1"))
        assert os.path.isdir(str(tmp_path))

    def test_symlinked_accession_dir_is_skipped_and_target_untouched(self, tmp_path):
        outside = tmp_path / "outside"
        victim = outside / "VICTIM"
        victim.mkdir(parents=True)
        (victim / "data.bin").write_bytes(b"x")
        _age(str(victim), EXPIRED)
        cache = tmp_path / "cache"
        project_dir = cache / "net-1" / "proj-a"
        project_dir.mkdir(parents=True)
        os.symlink(str(victim), str(project_dir / "ACC_LINK"))

        stats = sweep_expired_cache_entries(str(cache), TTL, now=NOW)

        assert (victim / "data.bin").exists()
        assert stats.accession_dirs_removed == 0

    def test_non_zip_stray_file_at_project_level_kept(self, tmp_path):
        net_dir = os.path.join(str(tmp_path), "net-1")
        os.makedirs(net_dir)
        stray = os.path.join(net_dir, "README.txt")
        with open(stray, "w") as fh:
            fh.write("x")
        _age(stray, EXPIRED)

        stats = sweep_expired_cache_entries(str(tmp_path), TTL, now=NOW)

        assert os.path.isfile(stray)
        assert stats.orphan_zips_removed == 0


class TestOrphanZips:
    def test_old_staging_zip_unlinked_fresh_kept(self, tmp_path):
        project_dir = os.path.join(str(tmp_path), "net-1", "proj-a")
        os.makedirs(project_dir)
        old_zip = os.path.join(project_dir, "ACC1-scans-NIFTI.zip")
        new_zip = os.path.join(project_dir, "ACC2-scans-NIFTI.zip")
        for path, mtime in ((old_zip, EXPIRED), (new_zip, FRESH)):
            with open(path, "wb") as fh:
                fh.write(b"x" * 10)
            _age(path, mtime)

        stats = sweep_expired_cache_entries(str(tmp_path), TTL, now=NOW)

        assert not os.path.exists(old_zip)
        assert os.path.isfile(new_zip)
        assert stats.orphan_zips_removed == 1
        assert stats.bytes_freed == 10


class TestEmptyProjectDirs:
    def test_emptied_project_dir_removed_nonempty_kept(self, tmp_path):
        _seed_accession(tmp_path, "net-1", "proj-gone", "ACC1", sentinel_mtimes=[EXPIRED])
        _seed_accession(tmp_path, "net-1", "proj-stays", "ACC2", sentinel_mtimes=[FRESH])

        stats = sweep_expired_cache_entries(str(tmp_path), TTL, now=NOW)

        assert not os.path.exists(os.path.join(str(tmp_path), "net-1", "proj-gone"))
        assert os.path.isdir(os.path.join(str(tmp_path), "net-1", "proj-stays"))
        assert stats.empty_project_dirs_removed == 1


class TestSweepRobustness:
    def test_per_item_failure_does_not_abort_the_pass(self, tmp_path):
        _seed_accession(tmp_path, "net-1", "proj-a", "ACC_A", sentinel_mtimes=[EXPIRED])
        _seed_accession(tmp_path, "net-1", "proj-a", "ACC_B", sentinel_mtimes=[EXPIRED])
        real_rmtree = shutil.rmtree
        calls = {"n": 0}

        def flaky_rmtree(path, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("disk went away")
            return real_rmtree(path, *args, **kwargs)

        with patch.object(cache_retention.shutil, "rmtree", side_effect=flaky_rmtree):
            stats = sweep_expired_cache_entries(str(tmp_path), TTL, now=NOW)

        assert stats.accession_dirs_removed == 1
        assert stats.errors == 1
        # Exactly one of the two dirs survived the flake.
        survivors = [
            d for d in ("ACC_A", "ACC_B") if os.path.exists(os.path.join(str(tmp_path), "net-1", "proj-a", d))
        ]
        assert len(survivors) == 1

    def test_missing_base_dir_returns_empty_stats(self, tmp_path):
        stats = sweep_expired_cache_entries(str(tmp_path / "never-created"), TTL, now=NOW)

        assert stats == SweepStats()

    def test_concurrent_deletion_is_benign(self, tmp_path):
        """A concurrent deleter (operator, outdated fl-client image) can empty dirs under us:
        ENOENT must not count as an error."""
        expired = _seed_accession(tmp_path, "net-1", "proj-a", "ACC1", sentinel_mtimes=[EXPIRED])
        real_rmtree = shutil.rmtree

        def racing_rmtree(path, *args, **kwargs):
            real_rmtree(path)  # someone else deletes it first...
            return real_rmtree(path, *args, **kwargs)  # ...then our call runs, hitting ENOENT

        with patch.object(cache_retention.shutil, "rmtree", side_effect=racing_rmtree):
            stats = sweep_expired_cache_entries(str(tmp_path), TTL, now=NOW)

        assert not os.path.exists(expired)
        assert stats.errors == 0


class TestSweeperLoop:
    @pytest.mark.asyncio
    async def test_loop_sweeps_then_sleeps_and_survives_a_failing_pass(self, monkeypatch):
        sweep = MagicMock(side_effect=RuntimeError("pass blew up"))
        monkeypatch.setattr(cache_retention, "sweep_expired_cache_entries", sweep)
        # End the loop at its first sleep: a real-time wait here would only slow the suite.
        monkeypatch.setattr(
            cache_retention.asyncio, "sleep", MagicMock(side_effect=asyncio.CancelledError)
        )

        with pytest.raises(asyncio.CancelledError):
            await cache_retention.run_cache_retention_sweeper()

        sweep.assert_called_once()
        args = sweep.call_args[0]
        assert args[0] == cache_retention.BASE_IMAGES_DOWNLOAD_DIR
        assert args[1] == pytest.approx(168.0 * 3600)

    @pytest.mark.asyncio
    async def test_interval_floored_at_one_minute(self, monkeypatch):
        monkeypatch.setattr(
            cache_retention.get_settings(), "IMAGE_CACHE_SWEEP_INTERVAL_MINUTES", 0.0
        )
        monkeypatch.setattr(cache_retention, "sweep_expired_cache_entries", MagicMock(return_value=SweepStats()))
        sleep = MagicMock(side_effect=asyncio.CancelledError)
        monkeypatch.setattr(cache_retention.asyncio, "sleep", sleep)

        with pytest.raises(asyncio.CancelledError):
            await cache_retention.run_cache_retention_sweeper()

        sleep.assert_called_once_with(60.0)


class TestEntryLastUsed:
    def test_prefers_newest_sentinel_over_dir_mtime(self, tmp_path):
        accession_dir = _seed_accession(
            tmp_path, "net-1", "proj-a", "ACC1", sentinel_mtimes=[EXPIRED, FRESH], dir_mtime=EXPIRED
        )

        assert cache_retention._entry_last_used(accession_dir) == pytest.approx(FRESH)

    def test_falls_back_to_dir_mtime_without_sentinels(self, tmp_path):
        accession_dir = _seed_accession(tmp_path, "net-1", "proj-a", "ACC1", dir_mtime=EXPIRED)

        assert cache_retention._entry_last_used(accession_dir) == pytest.approx(EXPIRED)


def test_module_snapshot_points_at_settings_dir():
    """The sweeper reads the same module-snapshot pattern as download.py; keep it wired."""
    assert cache_retention.BASE_IMAGES_DOWNLOAD_DIR
    assert isinstance(cache_retention.BASE_IMAGES_DOWNLOAD_DIR, str)


def test_now_defaults_to_wall_clock(tmp_path):
    """Without an injected clock, an entry just written must be fresh."""
    kept = _seed_accession(tmp_path, "net-1", "proj-a", "ACC1", sentinel_mtimes=[time.time()])

    stats = sweep_expired_cache_entries(str(tmp_path), TTL)

    assert os.path.isdir(kept)
    assert stats.accession_dirs_removed == 0
