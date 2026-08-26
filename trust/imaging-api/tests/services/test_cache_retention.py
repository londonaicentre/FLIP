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

    def test_project_dir_holding_only_an_expired_zip_is_emptied_then_removed(self, tmp_path):
        """The crash-orphan case end to end: zip unlinked, then the emptied project dir
        rmdir'd in the same pass — no empty-dir residue accumulates."""
        project_dir = os.path.join(str(tmp_path), "net-1", "proj-a")
        os.makedirs(project_dir)
        orphan = os.path.join(project_dir, "ACC1-scans-NIFTI.zip")
        with open(orphan, "wb") as fh:
            fh.write(b"x" * 10)
        _age(orphan, EXPIRED)

        stats = sweep_expired_cache_entries(str(tmp_path), TTL, now=NOW)

        assert not os.path.exists(project_dir)
        assert stats.orphan_zips_removed == 1
        assert stats.empty_project_dirs_removed == 1

    def test_concurrently_deleted_zip_is_benign(self, tmp_path):
        """A zip removed under us between scandir and unlink must not count as an error."""
        project_dir = os.path.join(str(tmp_path), "net-1", "proj-a")
        os.makedirs(project_dir)
        orphan = os.path.join(project_dir, "ACC1-scans-NIFTI.zip")
        with open(orphan, "wb") as fh:
            fh.write(b"x")
        _age(orphan, EXPIRED)

        with patch.object(cache_retention.os, "unlink", side_effect=FileNotFoundError):
            stats = sweep_expired_cache_entries(str(tmp_path), TTL, now=NOW)

        assert stats.errors == 0
        assert stats.orphan_zips_removed == 0

    def test_non_zip_stray_file_inside_project_dir_kept(self, tmp_path):
        project_dir = os.path.join(str(tmp_path), "net-1", "proj-a")
        os.makedirs(project_dir)
        stray = os.path.join(project_dir, "notes.txt")
        with open(stray, "w") as fh:
            fh.write("x")
        _age(stray, EXPIRED)

        stats = sweep_expired_cache_entries(str(tmp_path), TTL, now=NOW)

        assert os.path.isfile(stray)
        assert stats.orphan_zips_removed == 0


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

    def test_accession_dir_vanishing_before_its_stat_is_benign(self, tmp_path):
        """An accession dir deleted under us between scandir and last-used stat must be
        skipped without counting as an error."""
        _seed_accession(tmp_path, "net-1", "proj-a", "ACC1", sentinel_mtimes=[EXPIRED])

        with patch.object(cache_retention, "_entry_last_used", side_effect=FileNotFoundError):
            stats = sweep_expired_cache_entries(str(tmp_path), TTL, now=NOW)

        assert stats.errors == 0
        assert stats.accession_dirs_removed == 0

    def test_escaping_realpath_is_refused_loudly(self, tmp_path):
        """The belt-and-braces traversal guard must leave a trace when it fires — a
        silently-immortal cache entry is the failure mode this module exists to close."""
        _seed_accession(tmp_path, "net-1", "proj-a", "ACC1", sentinel_mtimes=[EXPIRED])
        stats = SweepStats()

        cache_retention._remove_expired_accession_dir("/somewhere/else/entirely", str(tmp_path), stats)

        assert stats.errors == 1
        assert stats.accession_dirs_removed == 0

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
    @pytest.fixture(autouse=True)
    def _clean_degraded_registry(self):
        from imaging_api.utils.background import reset_dead_background_tasks

        reset_dead_background_tasks()
        yield
        reset_dead_background_tasks()

    @pytest.mark.asyncio
    async def test_loop_sweeps_then_sleeps_and_survives_a_failing_pass(self, monkeypatch):
        steps = MagicMock(side_effect=RuntimeError("pass blew up"))
        monkeypatch.setattr(cache_retention, "_sweep_steps", steps)
        # End the loop at its first sleep: a real-time wait here would only slow the suite.
        monkeypatch.setattr(cache_retention.asyncio, "sleep", MagicMock(side_effect=asyncio.CancelledError))

        with pytest.raises(asyncio.CancelledError):
            await cache_retention.run_cache_retention_sweeper()

        steps.assert_called_once()
        args = steps.call_args[0]
        assert args[0] == cache_retention.BASE_IMAGES_DOWNLOAD_DIR
        assert args[1] == pytest.approx(168.0 * 3600)
        assert isinstance(args[3], SweepStats)

    @pytest.mark.asyncio
    async def test_loop_yields_between_items(self, monkeypatch, tmp_path):
        """The driving loop must await between generator steps so a mass expiry can't
        starve /health — one asyncio.sleep(0) per handled item, then the interval sleep."""
        _seed_accession(tmp_path, "net-1", "proj-a", "ACC_A", sentinel_mtimes=[time.time()])
        _seed_accession(tmp_path, "net-1", "proj-a", "ACC_B", sentinel_mtimes=[time.time()])
        monkeypatch.setattr(cache_retention, "BASE_IMAGES_DOWNLOAD_DIR", str(tmp_path))
        sleep_calls = []

        async def fake_sleep(delay):
            sleep_calls.append(delay)
            if delay > 0:  # the interval sleep ends the test
                raise asyncio.CancelledError

        monkeypatch.setattr(cache_retention.asyncio, "sleep", fake_sleep)

        with pytest.raises(asyncio.CancelledError):
            await cache_retention.run_cache_retention_sweeper()

        assert sleep_calls.count(0) == 2  # one yield per accession dir handled
        assert sleep_calls[-1] > 0

    @pytest.mark.asyncio
    async def test_interval_floored_at_one_minute(self, monkeypatch):
        monkeypatch.setattr(cache_retention.get_settings(), "IMAGE_CACHE_SWEEP_INTERVAL_MINUTES", 0.0)
        monkeypatch.setattr(cache_retention, "_sweep_steps", MagicMock(return_value=iter([])))
        sleep = MagicMock(side_effect=asyncio.CancelledError)
        monkeypatch.setattr(cache_retention.asyncio, "sleep", sleep)

        with pytest.raises(asyncio.CancelledError):
            await cache_retention.run_cache_retention_sweeper()

        sleep.assert_called_once_with(60.0)


class TestFailingStreakEscalation:
    """A sweep that stays alive but keeps failing must degrade /health — hourly error
    logs on a trust host nobody tails are not an escalation path."""

    @pytest.fixture(autouse=True)
    def _clean_degraded_registry(self):
        from imaging_api.utils.background import reset_dead_background_tasks

        reset_dead_background_tasks()
        yield
        reset_dead_background_tasks()

    @staticmethod
    def _drive(monkeypatch, per_pass_errors):
        """Run the loop for len(per_pass_errors) passes, then cancel at the next sleep."""

        def make_steps(errors_iter):
            def steps(_base, _ttl, _now, stats):
                stats.errors = next(errors_iter)
                return iter([])

            return steps

        monkeypatch.setattr(cache_retention, "_sweep_steps", make_steps(iter(per_pass_errors)))
        remaining = {"sleeps": len(per_pass_errors)}

        async def fake_sleep(_delay):
            remaining["sleeps"] -= 1
            if remaining["sleeps"] <= 0:
                raise asyncio.CancelledError

        monkeypatch.setattr(cache_retention.asyncio, "sleep", fake_sleep)

    @pytest.mark.asyncio
    async def test_three_failing_passes_mark_degraded(self, monkeypatch):
        from imaging_api.utils.background import dead_background_tasks

        self._drive(monkeypatch, per_pass_errors=[2, 1, 3])

        with pytest.raises(asyncio.CancelledError):
            await cache_retention.run_cache_retention_sweeper()

        assert cache_retention.SWEEP_FAILING_HEALTH_NAME in dead_background_tasks()

    @pytest.mark.asyncio
    async def test_two_failing_passes_do_not_mark(self, monkeypatch):
        from imaging_api.utils.background import dead_background_tasks

        self._drive(monkeypatch, per_pass_errors=[2, 1])

        with pytest.raises(asyncio.CancelledError):
            await cache_retention.run_cache_retention_sweeper()

        assert dead_background_tasks() == set()

    @pytest.mark.asyncio
    async def test_clean_pass_clears_the_marker(self, monkeypatch):
        from imaging_api.utils.background import dead_background_tasks

        self._drive(monkeypatch, per_pass_errors=[1, 1, 1, 0])

        with pytest.raises(asyncio.CancelledError):
            await cache_retention.run_cache_retention_sweeper()

        assert dead_background_tasks() == set()


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
