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

"""The image-cache sweep (FLIP#1050): an accession dir goes once its newest sentinel is older than the TTL."""

import asyncio
import os
from pathlib import Path

from imaging_api.services.cache_retention import sweep as _sweep
from imaging_api.services.image_cache import SENTINEL_PREFIX

TTL = 3600.0
NOW = 1_000_000.0


def sweep(base_dir: str, ttl_seconds: float, now: float) -> int:
    return asyncio.run(_sweep(base_dir, ttl_seconds, now=now))


def _study(base: Path, accession: str, age: float, sentinels: tuple[float, ...] = ()) -> Path:
    """A cached study under net-1/project/<accession>, its dir ``age`` seconds old, with sentinels of the given ages."""
    study = base / "net-1" / "project" / accession
    (study / "scans").mkdir(parents=True)
    (study / "scans" / "1.dcm").write_bytes(b"x")
    for i, sentinel_age in enumerate(sentinels):
        sentinel = study / f"{SENTINEL_PREFIX}scan-{i}"
        sentinel.write_text("{}")
        os.utime(sentinel, (NOW - sentinel_age, NOW - sentinel_age))
    os.utime(study, (NOW - age, NOW - age))
    return study


def test_removes_a_study_whose_sentinel_is_older_than_the_ttl(tmp_path):
    old = _study(tmp_path, "OLD", age=10 * TTL, sentinels=(2 * TTL,))
    fresh = _study(tmp_path, "FRESH", age=10 * TTL, sentinels=(TTL / 2,))

    assert sweep(str(tmp_path), TTL, now=NOW) == 1
    assert not old.exists()
    assert fresh.exists()


def test_the_newest_sentinel_governs(tmp_path):
    study = _study(tmp_path, "TWO", age=10 * TTL, sentinels=(5 * TTL, TTL / 2))

    assert sweep(str(tmp_path), TTL, now=NOW) == 0
    assert study.exists()


def test_a_directory_being_written_is_not_expired(tmp_path):
    """A half-extracted study has an old sentinel for another resource but a fresh directory mtime."""
    study = _study(tmp_path, "BUSY", age=0, sentinels=(5 * TTL,))

    assert sweep(str(tmp_path), TTL, now=NOW) == 0
    assert study.exists()


def test_a_sentinel_less_directory_goes_by_its_own_mtime(tmp_path):
    old = _study(tmp_path, "HALF", age=2 * TTL)

    assert sweep(str(tmp_path), TTL, now=NOW) == 1
    assert not old.exists()


def test_never_touches_the_upload_dir_or_net_dirs(tmp_path):
    upload = tmp_path / "net-1" / "upload" / "ACC"
    upload.mkdir(parents=True)
    os.utime(upload, (NOW - 10 * TTL, NOW - 10 * TTL))
    _study(tmp_path, "OLD", age=10 * TTL, sentinels=(2 * TTL,))

    assert sweep(str(tmp_path), TTL, now=NOW) == 1
    assert upload.exists()
    assert (tmp_path / "net-1" / "project").is_dir()


def test_a_missing_base_dir_is_nothing_to_sweep(tmp_path):
    assert sweep(str(tmp_path / "absent"), TTL, now=NOW) == 0
