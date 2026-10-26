# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
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

"""Per-job cohort data, staged where the job's pods mount it (FLIP#1678).

Before a job starts, the hub has each of its trusts write the job's approved dataframe here; when the model ends it
has them remove it. A Kubernetes trust running NVFLARE job pods mounts a project's directory, read-only, into that
project's job pods. Unlike the snapshot store, which holds identifiers only, this holds clinical rows, so it lives
only while a job runs: a sweep removes anything older than ``COHORT_DATA_MAX_AGE_HOURS``.

Layout::

    <COHORT_DATA_DIR>/<project-uuid>/<model-uuid>/dataframe.parquet

Work directories start with a dot (``.tmp-``, ``.del-``), so a reader can tell a staged model from one being written.
An unset ``COHORT_DATA_DIR`` disables staging: on a trust whose jobs read their data from the APIs (Compose), the
stage task is a no-op.
"""

import os
import shutil
import time
import uuid
from pathlib import Path

import pandas as pd

from data_access_api.config import get_settings
from data_access_api.utils.logger import logger

DATAFRAME_FILENAME = "dataframe.parquet"
_TMP_PREFIX = ".tmp-"
_DEL_PREFIX = ".del-"
# Rows of patient data: never readable by others, whatever the process umask.
_UMASK = 0o027


def staging_enabled() -> bool:
    """Whether this trust stages job data at all."""
    return bool(get_settings().COHORT_DATA_DIR)


def _root() -> Path:
    return Path(get_settings().COHORT_DATA_DIR)


def _canonical(value: str) -> str:
    """The id as a lowercase UUID string, so it is always one safe path component."""
    return str(uuid.UUID(value))


def _remove(path: Path) -> None:
    """Remove a directory without a reader ever seeing it half-deleted: rename it aside first."""
    aside = path.with_name(f"{_DEL_PREFIX}{path.name}-{uuid.uuid4().hex}")
    os.replace(path, aside)
    shutil.rmtree(aside, ignore_errors=True)


def stage_dataframe(project_id: str, model_id: str, df: pd.DataFrame) -> Path:
    """Write a job's dataframe, replacing any earlier copy for the same model in one rename.

    Args:
        project_id (str): The hub project id.
        model_id (str): The hub model id.
        df (pd.DataFrame): The approved cohort, already filtered and threshold-checked.

    Returns:
        Path: The written file.

    Raises:
        ValueError: if either id is not a UUID.
    """
    project, model = _canonical(project_id), _canonical(model_id)
    previous_umask = os.umask(_UMASK)
    try:
        project_dir = _root() / project
        project_dir.mkdir(parents=True, exist_ok=True)
        work = project_dir / f"{_TMP_PREFIX}{model}-{uuid.uuid4().hex}"
        work.mkdir()
        try:
            df.to_parquet(work / DATAFRAME_FILENAME, index=False)
            final = project_dir / model
            if final.exists():
                _remove(final)
            os.replace(work, final)
        finally:
            shutil.rmtree(work, ignore_errors=True)
    finally:
        os.umask(previous_umask)
    return final / DATAFRAME_FILENAME


def unstage(project_id: str, model_id: str) -> bool:
    """Remove a job's dataframe, and the project's directory with its last model.

    Args:
        project_id (str): The hub project id.
        model_id (str): The hub model id.

    Returns:
        bool: True if something was removed.

    Raises:
        ValueError: if either id is not a UUID.
    """
    project_dir = _root() / _canonical(project_id)
    model_dir = project_dir / _canonical(model_id)
    if not model_dir.exists():
        return False
    _remove(model_dir)
    _remove_if_empty(project_dir)
    return True


def _remove_if_empty(project_dir: Path) -> None:
    try:
        project_dir.rmdir()
    except OSError:
        pass  # Another model is still staged here, or it is already gone.


def sweep(max_age_hours: int, now: float | None = None) -> int:
    """Remove model and work directories older than the cap, and project directories left empty.

    Args:
        max_age_hours (int): The cap. It must exceed the longest job.
        now (float | None): The current time, for tests.

    Returns:
        int: How many model or work directories were removed.
    """
    root = _root()
    if not root.is_dir():
        return 0
    cutoff = (time.time() if now is None else now) - max_age_hours * 3600
    removed = 0
    for project_dir in root.iterdir():
        if not project_dir.is_dir():
            continue
        for entry in project_dir.iterdir():
            try:
                if entry.is_dir() and entry.stat().st_mtime < cutoff:
                    shutil.rmtree(entry)
                    removed += 1
            except FileNotFoundError:
                continue
        _remove_if_empty(project_dir)
    if removed:
        logger.info(f"Cohort data sweep removed {removed} director{'y' if removed == 1 else 'ies'}")
    return removed


def ensure_data_dir() -> None:
    """Create the data directory at boot. Never raises: staging then fails per task, which the hub reports."""
    if not staging_enabled():
        return
    try:
        _root().mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.exception(f"Cannot create the cohort data directory {_root()}; staging will fail until it can")
