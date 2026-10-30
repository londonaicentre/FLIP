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

"""Route tests for staging a job's approved cohort dataframe (FLIP#1678)."""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from data_access_api.main import app
from data_access_api.routers.cohort import _BELOW_THRESHOLD_DETAIL
from tests.conftest import AUTH_HEADERS, WRITE_AUTH_HEADERS
from tests.routers.test_cohort_snapshot import FROZEN_QUERY, PROJECT_UUID, _snapshot

client = TestClient(app)

MODEL_UUID = "1f0b9d1c-7d0a-4c9e-9f49-0b3a3f6f1a22"
STAGE_BODY = {"encrypted_project_id": "sealed", "model_id": MODEL_UUID}


@pytest.fixture
def data_dir(tmp_path):
    with patch("data_access_api.services.cohort_data.get_settings") as mock_settings:
        mock_settings.return_value.COHORT_DATA_DIR = str(tmp_path)
        mock_settings.return_value.COHORT_DATA_MAX_AGE_HOURS = 168
        yield tmp_path


@pytest.fixture
def approved_cohort():
    """The /cohort/dataframe collaborators, mocked as test_cohort_snapshot.py mocks them."""
    live = pd.DataFrame({"person_id": [1, 2, 3], "label": [0, 1, 0]})
    with (
        patch("data_access_api.routers.cohort.get_settings") as mock_get_settings,
        patch("data_access_api.routers.cohort.decrypt", return_value=PROJECT_UUID),
        patch("data_access_api.routers.cohort.get_snapshot", return_value=_snapshot(person_ids=["1", "2", "3"])),
        patch("data_access_api.routers.cohort.validate_query"),
        patch("data_access_api.routers.cohort.get_records", return_value=live) as mock_get_records,
    ):
        mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 2
        yield {"live": live, "settings": mock_get_settings, "get_records": mock_get_records}


def test_stage_writes_parquet_for_the_approved_cohort(data_dir, approved_cohort):
    served = client.post(
        "/cohort/dataframe", json={"encrypted_project_id": "sealed", "query": FROZEN_QUERY}, headers=AUTH_HEADERS
    ).json()

    response = client.post("/cohort/stage", json=STAGE_BODY, headers=WRITE_AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json() == {"staged": True, "rows": 3}
    staged = pd.read_parquet(data_dir / PROJECT_UUID / MODEL_UUID / "dataframe.parquet")
    assert staged.to_dict(orient="list") == served == {"person_id": [1, 2, 3], "label": [0, 1, 0]}


def test_stage_refuses_below_threshold_with_the_fixed_reason(data_dir, approved_cohort):
    approved_cohort["settings"].return_value.COHORT_QUERY_THRESHOLD = 10

    response = client.post("/cohort/stage", json=STAGE_BODY, headers=WRITE_AUTH_HEADERS)

    assert response.status_code == 403
    assert response.json()["detail"] == _BELOW_THRESHOLD_DETAIL
    assert list(data_dir.iterdir()) == []


def test_stage_refuses_a_policy_denial_the_same_way(data_dir, approved_cohort):
    with patch("data_access_api.routers.cohort._evaluate", return_value=MagicMock(permit=False)):
        response = client.post("/cohort/stage", json=STAGE_BODY, headers=WRITE_AUTH_HEADERS)

    assert response.status_code == 403
    assert response.json()["detail"] == _BELOW_THRESHOLD_DETAIL
    assert list(data_dir.iterdir()) == []
    approved_cohort["get_records"].assert_not_called()


def test_stage_is_a_noop_when_staging_is_disabled(approved_cohort):
    with patch("data_access_api.services.cohort_data.get_settings") as mock_settings:
        mock_settings.return_value.COHORT_DATA_DIR = ""
        response = client.post("/cohort/stage", json=STAGE_BODY, headers=WRITE_AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json() == {"staged": False, "rows": 0}
    approved_cohort["get_records"].assert_not_called()


def test_stage_rejects_a_model_id_that_is_not_a_uuid(data_dir, approved_cohort):
    response = client.post("/cohort/stage", json={**STAGE_BODY, "model_id": "../../etc"}, headers=WRITE_AUTH_HEADERS)

    assert response.status_code == 422
    assert list(data_dir.iterdir()) == []


def test_unstage_deletes_the_staged_model(data_dir, approved_cohort):
    client.post("/cohort/stage", json=STAGE_BODY, headers=WRITE_AUTH_HEADERS)

    response = client.post("/cohort/stage/delete", json=STAGE_BODY, headers=WRITE_AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json() == {"deleted": True}
    assert not (data_dir / PROJECT_UUID).exists()


def test_unstage_is_a_noop_when_staging_is_disabled(approved_cohort):
    with patch("data_access_api.services.cohort_data.get_settings") as mock_settings:
        mock_settings.return_value.COHORT_DATA_DIR = ""
        response = client.post("/cohort/stage/delete", json=STAGE_BODY, headers=WRITE_AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json() == {"deleted": False}


def test_stage_refuses_a_project_id_the_store_cannot_name(data_dir, approved_cohort):
    with patch("data_access_api.routers.cohort.cohort_data.stage_dataframe", side_effect=ValueError("not a uuid")):
        response = client.post("/cohort/stage", json=STAGE_BODY, headers=WRITE_AUTH_HEADERS)

    assert response.status_code == 400
    assert response.json()["detail"] == "Project id is not a valid UUID."


def test_stage_reports_a_write_failure_without_its_cause(data_dir, approved_cohort):
    with patch("data_access_api.routers.cohort.cohort_data.stage_dataframe", side_effect=OSError(28, "No space left")):
        response = client.post("/cohort/stage", json=STAGE_BODY, headers=WRITE_AUTH_HEADERS)

    assert response.status_code == 500
    assert response.json()["detail"] == "Could not stage the cohort data."


def test_unstage_refuses_a_project_id_the_store_cannot_name(data_dir, approved_cohort):
    with patch("data_access_api.routers.cohort.cohort_data.unstage", side_effect=ValueError("not a uuid")):
        response = client.post("/cohort/stage/delete", json=STAGE_BODY, headers=WRITE_AUTH_HEADERS)

    assert response.status_code == 400
    assert response.json()["detail"] == "Project id is not a valid UUID."
