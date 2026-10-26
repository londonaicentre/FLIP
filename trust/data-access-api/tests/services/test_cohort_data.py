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

"""The per-job cohort data store (FLIP#1678)."""

import os
import stat
import time
from unittest.mock import patch

import pandas as pd
import pytest

from data_access_api.services import cohort_data

PROJECT = "8b2e9d6e-5a53-4f2e-9c37-2c8f4f0f2d11"
MODEL = "1f0b9d1c-7d0a-4c9e-9f49-0b3a3f6f1a22"
OTHER_MODEL = "2a1c8e2d-6e1b-4d8f-8a38-1c4b4e7e2b33"


@pytest.fixture
def data_dir(tmp_path):
    with patch("data_access_api.services.cohort_data.get_settings") as mock_settings:
        mock_settings.return_value.COHORT_DATA_DIR = str(tmp_path)
        mock_settings.return_value.COHORT_DATA_MAX_AGE_HOURS = 168
        yield tmp_path


def test_staging_is_enabled_only_with_a_directory(data_dir):
    assert cohort_data.staging_enabled() is True
    with patch("data_access_api.services.cohort_data.get_settings") as mock_settings:
        mock_settings.return_value.COHORT_DATA_DIR = ""
        assert cohort_data.staging_enabled() is False


def test_stage_writes_parquet_under_project_and_model(data_dir):
    frame = pd.DataFrame({"person_id": [1, 2], "label": ["a", "b"]})

    path = cohort_data.stage_dataframe(PROJECT, MODEL, frame)

    assert path == data_dir / PROJECT / MODEL / "dataframe.parquet"
    assert pd.read_parquet(path).to_dict(orient="list") == {"person_id": [1, 2], "label": ["a", "b"]}


def test_staged_files_are_not_readable_by_others(data_dir):
    path = cohort_data.stage_dataframe(PROJECT, MODEL, pd.DataFrame({"person_id": [1]}))

    for entry in (path, path.parent, path.parent.parent):
        assert stat.S_IMODE(entry.stat().st_mode) & 0o007 == 0, entry


def test_restage_replaces_atomically(data_dir):
    # Review Focus 1: a retried start re-stages the same model; the new data wins and no work directory is left.
    cohort_data.stage_dataframe(PROJECT, MODEL, pd.DataFrame({"person_id": [1]}))

    path = cohort_data.stage_dataframe(PROJECT, MODEL, pd.DataFrame({"person_id": [7, 8]}))

    assert pd.read_parquet(path)["person_id"].tolist() == [7, 8]
    assert sorted(os.listdir(data_dir / PROJECT)) == [MODEL]


@pytest.mark.parametrize(("project", "model"), [("../etc", MODEL), (PROJECT, "x/../../y"), ("not-a-uuid", MODEL)])
def test_stage_refuses_ids_that_are_not_uuids(data_dir, project, model):
    with pytest.raises(ValueError, match="badly formed"):
        cohort_data.stage_dataframe(project, model, pd.DataFrame({"person_id": [1]}))
    assert list(data_dir.iterdir()) == []


def test_unstage_removes_the_model_then_the_empty_project_dir(data_dir):
    cohort_data.stage_dataframe(PROJECT, MODEL, pd.DataFrame({"person_id": [1]}))

    assert cohort_data.unstage(PROJECT, MODEL) is True

    assert not (data_dir / PROJECT).exists()


def test_unstage_keeps_the_project_dir_while_another_model_is_staged(data_dir):
    cohort_data.stage_dataframe(PROJECT, MODEL, pd.DataFrame({"person_id": [1]}))
    cohort_data.stage_dataframe(PROJECT, OTHER_MODEL, pd.DataFrame({"person_id": [2]}))

    cohort_data.unstage(PROJECT, MODEL)

    assert sorted(os.listdir(data_dir / PROJECT)) == [OTHER_MODEL]


def test_unstage_of_nothing_reports_false(data_dir):
    assert cohort_data.unstage(PROJECT, MODEL) is False


def test_sweep_removes_model_dirs_older_than_the_cap_and_stale_work_dirs(data_dir):
    old = cohort_data.stage_dataframe(PROJECT, MODEL, pd.DataFrame({"person_id": [1]})).parent
    young = cohort_data.stage_dataframe(PROJECT, OTHER_MODEL, pd.DataFrame({"person_id": [2]})).parent
    stale_work = data_dir / PROJECT / f".tmp-{MODEL}-abc"
    stale_work.mkdir()
    ten_days_ago = time.time() - 10 * 24 * 3600
    for entry in (old, stale_work):
        os.utime(entry, (ten_days_ago, ten_days_ago))

    removed = cohort_data.sweep(max_age_hours=168)

    assert removed == 2
    assert not old.exists()
    assert not stale_work.exists()
    assert young.exists()


def test_sweep_removes_a_project_dir_it_empties(data_dir):
    old = cohort_data.stage_dataframe(PROJECT, MODEL, pd.DataFrame({"person_id": [1]})).parent
    ten_days_ago = time.time() - 10 * 24 * 3600
    os.utime(old, (ten_days_ago, ten_days_ago))

    cohort_data.sweep(max_age_hours=168)

    assert list(data_dir.iterdir()) == []


def test_ensure_data_dir_creates_the_directory_and_never_raises(tmp_path):
    target = tmp_path / "cohort-data"
    with patch("data_access_api.services.cohort_data.get_settings") as mock_settings:
        mock_settings.return_value.COHORT_DATA_DIR = str(target)
        cohort_data.ensure_data_dir()
        assert target.is_dir()
        mock_settings.return_value.COHORT_DATA_DIR = "/proc/definitely/not/writable"
        cohort_data.ensure_data_dir()


def test_stage_never_touches_the_process_umask(data_dir):
    # The umask is process-wide and routes run on a threadpool: changing it would race every other request's writes.
    with patch("data_access_api.services.cohort_data.os.umask", side_effect=AssertionError("umask touched")):
        path = cohort_data.stage_dataframe(PROJECT, MODEL, pd.DataFrame({"person_id": [1]}))

    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o750
    assert stat.S_IMODE(path.parent.parent.stat().st_mode) == 0o750
